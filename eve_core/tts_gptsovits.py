"""GPT-SoVITS 合成后端。

和 `PiperTts` / `CosyVoiceTts` 保持同一套接口，`eve_ui/tts.py` 只管换一行：

    synthesize(text) -> (numpy float32 波形, 采样率)

为什么用它：CosyVoice 300M 的零样本复刻吐字糊；
GPT-SoVITS 用同样的几秒钟参考就能把字咬清楚，而且支持 v2 预训练 + 微调，
以后拿更多录音继续训，音色会更稳。

依赖：`.vendor/GPT-SoVITS`（gitignore，不下库）+ `GPT_SoVITS/pretrained_models` 里的权重。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / ".vendor" / "GPT-SoVITS"
PRETRAINED = VENDOR / "GPT_SoVITS" / "pretrained_models"

# v2 预训练的固定文件名
T2S = "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
VITS = "gsv-v2final-pretrained/s2G2333k.pth"
HUBERT = "chinese-hubert-base"
BERT = "chinese-roberta-wwm-ext-large"

class AttrDict(dict):
    """既能 a.b 也能 a["b"]。

    导出微调权重时，config 要同时满足 savee（属性写法）和推理端（下标写法），
    而且必须能被 pickle —— 所以要放在这个模块里，别写在脚本的 __main__。
    """

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


def wrap_attrdict(obj):
    if isinstance(obj, dict):
        return AttrDict({k: wrap_attrdict(v) for k, v in obj.items()})
    return obj


REQUIRED = (T2S, VITS, f"{HUBERT}/pytorch_model.bin", f"{BERT}/pytorch_model.bin")


class GptSovitsTts:
    """GPT-SoVITS 的薄封装。"""

    # 支持流式（边生成边播），但默认跟着全局设置走
    stream_playback = False

    def __init__(
        self,
        ref_wav: str | Path,
        ref_text: str,
        text_lang: str = "zh",
        prompt_lang: str = "zh",
        speed: float = 1.0,
        volume: float = 1.0,
        half: bool = True,
        device: str = "cuda",
        t2s_path: str | Path = "",
        vits_path: str | Path = "",
        fx=None,
        repo: str | Path = "",
    ):
        self.repo = Path(repo) if repo else VENDOR
        # 构造时就转成绝对路径：合成期间 cwd 会切到仓库根，相对路径会解析错
        self.ref_wav = Path(ref_wav).resolve() if ref_wav else Path()
        self.ref_text = str(ref_text or "").strip()
        self.text_lang = text_lang or "zh"
        self.prompt_lang = prompt_lang or self.text_lang
        self.speed = max(0.5, min(2.0, float(speed)))
        self.volume = max(0.0, min(1.5, float(volume)))
        self.half = bool(half)
        self.device = device
        # 微调后的权重（留空就用 v2 预训练）
        self.t2s_path = Path(t2s_path).resolve() if t2s_path else None
        self.vits_path = Path(vits_path).resolve() if vits_path else None
        self.fx = fx
        self.error = ""
        self._tts = None
        self._sample_rate = 32000

    # ---------- 可用性 ----------

    @property
    def available(self) -> bool:
        if not (self.repo / "GPT_SoVITS" / "TTS_infer_pack" / "TTS.py").exists():
            self.error = f"没找到 GPT-SoVITS 代码：{self.repo}"
            return False
        pre = self.repo / "GPT_SoVITS" / "pretrained_models"
        missing = [name for name in REQUIRED if not (pre / name).exists()]
        if missing:
            self.error = f"预训练权重不全，缺：{'、'.join(missing)}"
            return False
        if not self.ref_wav or not self.ref_wav.exists():
            self.error = f"参考音频不存在：{self.ref_wav}"
            return False
        try:
            import torch  # noqa: F401
        except Exception as exc:
            self.error = f"缺少 torch：{exc}"
            return False
        return True

    # ---------- 加载 ----------

    @staticmethod
    def _patch_nltk_tag() -> None:
        """英文前端要 nltk 的 averaged_perceptron_tagger，本机下不到就先打个补丁。

        正版 nltk 数据（cmudict / tagger）从 GitHub 拉不下来（被墙 + SSRF 拦截），
        cmudict 已经用 PyPI 的 cmudict 包补上；tagger 补不了，
        退化成"所有词都当名词"——只会影响 read/lead 这种多音词的读音选择，
        音色和流利度不受影响。
        """
        try:
            import nltk

            nltk.pos_tag(["probe"])
            return  # 数据齐全
        except Exception:
            pass

        def _naive_pos_tag(tokens, tagset=None, lang="eng"):
            return [(word, "NN") for word in tokens]

        import nltk

        nltk.pos_tag = _naive_pos_tag
        for module_name in ("g2p_en", "g2p_en.g2p"):
            try:
                module = __import__(module_name, fromlist=["pos_tag"])
                module.pos_tag = _naive_pos_tag
            except Exception:
                continue

    def _paths(self) -> None:
        self._patch_nltk_tag()
        # GPT-SoVITS 的模块是按 "GPT_SoVITS/" 和仓库根目录两个视角 import 的
        for path in (self.repo / "GPT_SoVITS", self.repo, self.repo / "GPT_SoVITS" / "eres2net"):
            text = str(path)
            if text not in sys.path:
                sys.path.insert(0, text)

    def _ensure(self):
        if self._tts is not None:
            return self._tts
        self._paths()
        from TTS_infer_pack.TTS import TTS, TTS_Config

        pre = self.repo / "GPT_SoVITS" / "pretrained_models"
        configs = {
            "device": self.device,
            "is_half": self.half,
            "version": "v2",
            "t2s_weights_path": str(self.t2s_path or (pre / T2S)),
            "vits_weights_path": str(self.vits_path or (pre / VITS)),
            "cnhuhbert_base_path": str(pre / HUBERT),
            "bert_base_path": str(pre / BERT),
        }
        # 上游代码里有几处写死了相对路径（比如 sv.py 的 "GPT_SoVITS/pretrained_models/sv/..."），
        # 加载期间临时把工作目录切到仓库根，加载完切回来。
        import os

        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            self._tts = TTS(TTS_Config(configs))
            # TTS_Config 里写的路径会被版本预设覆盖，微调权重必须显式再加载一遍
            if self.vits_path is not None:
                self._tts.init_vits_weights(str(self.vits_path))
            if self.t2s_path is not None:
                self._tts.init_t2s_weights(str(self.t2s_path))
        finally:
            os.chdir(cwd)
        return self._tts

    # ---------- 合成 ----------

    def _request(self, text: str, streaming: bool) -> dict:
        return {
            "text": text,
            "text_lang": self.text_lang,
            # 必须绝对路径：合成期间我们把 cwd 切到了仓库根，相对路径会找不到
            "ref_audio_path": str(self.ref_wav),
            "prompt_text": self.ref_text,
            "prompt_lang": self.prompt_lang,
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 1.0,
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": self.speed,
            "streaming_mode": 3 if streaming else False,
            "seed": -1,
            "parallel_infer": True,
            "repetition_penalty": 1.35,
        }

    def stream_synthesize(self, text: str):
        """边生成边吐音频块（GPT-SoVITS 的流式质量不错）。"""
        import numpy as np

        from eve_core.tts import clean_for_speech

        spoken = clean_for_speech(text)
        if not spoken:
            return
        model = self._ensure()
        for rate, audio in model.run(self._request(spoken, streaming=True)):
            piece = np.asarray(audio)
            if piece.dtype != np.float32:
                # GPT-SoVITS 吐 int16
                piece = piece.astype(np.float32) / 32768.0
            piece = piece.reshape(-1)
            if piece.size:
                self._sample_rate = int(rate) or self._sample_rate
                yield piece, self._sample_rate

    def synthesize(self, text: str):
        """返回 (float32 波形, 采样率)；失败返回 (None, 0)。"""
        import numpy as np

        from eve_core.tts import clean_for_speech

        spoken = clean_for_speech(text)
        if not spoken:
            return None, 0
        try:
            model = self._ensure()
            pieces = []
            # 上游有几处写死相对路径（G2PWModel 等），合成期间同样切到仓库根
            import os

            cwd = os.getcwd()
            os.chdir(self.repo)
            try:
                for rate, audio in model.run(self._request(spoken, streaming=False)):
                    piece = np.asarray(audio)
                    if piece.dtype != np.float32:
                        piece = piece.astype(np.float32) / 32768.0
                    pieces.append(piece.reshape(-1))
                    self._sample_rate = int(rate) or self._sample_rate
            finally:
                os.chdir(cwd)
            if not pieces:
                self.error = "模型没有返回音频"
                return None, 0
            wave = np.concatenate(pieces).astype(np.float32)
            if self.fx is not None and getattr(self.fx, "enabled", False):
                try:
                    wave = self.fx.apply(wave, self._sample_rate)
                except Exception:
                    pass
            if self.volume != 1.0:
                wave = np.clip(wave * self.volume, -1.0, 1.0)
            return wave, self._sample_rate
        except Exception as exc:
            self.error = f"GPT-SoVITS 合成失败：{type(exc).__name__}: {exc}"
            return None, 0

    # ---------- 播放接口（和 PiperTts 一致） ----------

    def new_session(self, on_start=None, on_done=None, on_error=None):
        from eve_core.tts import PlaybackSession

        session = PlaybackSession(self, on_start=on_start, on_done=on_done, on_error=on_error)
        session.start()
        return session

    def speak(self, text: str, on_start=None, on_error=None) -> bool:
        session = self.new_session(on_start=on_start, on_error=on_error)
        session.push(text)
        session.close()
        session.start()
        session.join()
        return session.played
