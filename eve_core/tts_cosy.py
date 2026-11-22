"""CosyVoice2 合成后端。

和 `PiperTts` 保持同一个接口（`available` / `synthesize`），
`eve_ui/tts.py` 只换一行就能切过来：

    synthesize(text, speaker=None) -> (numpy float32 波形, 采样率)

为什么换：sherpa-onnx 的 VITS 音色是"能用"，但语气平、断句机械；
CosyVoice2 是 0.5B 的自回归 TTS，念长句自然得多，后面还能用 3–10 秒参考音频做零样本复刻。

代价：模型 3.6GB、首次加载要十几秒、占 2–3GB 显存。
所以它是**可选后端**，配置里 `tts_backend = "cosyvoice"` 才起；
加载失败会自动退回原来的 VITS。
"""

from __future__ import annotations

import sys
from pathlib import Path

# PyPI 上那个 `cosyvoice` 不是官方推理库（只有 api/server），真正的代码在官方仓库里。
# 所以把仓库放在 .vendor/CosyVoice，import 前加进 sys.path。
VENDOR = Path(__file__).resolve().parents[1] / ".vendor" / "CosyVoice"


def _stub_missing_training_deps() -> None:
    """cosyvoice 的 dataset 模块会 import 训练用的库，推理其实用不到。

    比如 pyworld（提取音高）在 Windows + Python 3.11 上没有预编译包，
    装不上；直接 stub 掉，等真要用训练功能时再报错。
    """
    import types

    try:
        import pyworld  # noqa: F401
    except ImportError:
        stub = types.ModuleType("pyworld")
        stub.__version__ = "0.0.0-stub"

        def _unavailable(*_args, **_kwargs):
            raise RuntimeError("pyworld 没装（只在训练/提取音高时用，推理不需要）")

        for name in ("harvest", "dio", "stonemask", "wav2world", "cheaptrick", "synthesize"):
            setattr(stub, name, _unavailable)
        sys.modules["pyworld"] = stub


def _ensure_vendor_path() -> None:
    _stub_missing_training_deps()
    for path in (VENDOR, VENDOR / "third_party" / "Matcha-TTS"):
        text = str(path)
        if path.exists() and text not in sys.path:
            sys.path.insert(0, text)


# CosyVoice2-0.5B **没有内置说话人**（spk2info 是空的），
# 所以默认走零样本复刻：给一段参考音频 + 它的文字，模型照着那个音色念。
DEFAULT_SPEAKER = "中文女"
DEFAULT_REF_TEXT = "希望你以后能够做的比我还好呦。"
DEFAULT_REF_WAV = VENDOR / "asset" / "zero_shot_prompt.wav"

# 少这几个文件就说明没下全，别硬加载
REQUIRED_V2 = ("llm.pt", "flow.pt", "hift.pt", "campplus.onnx", "cosyvoice2.yaml")
REQUIRED_V1 = ("llm.pt", "flow.pt", "hift.pt", "campplus.onnx", "cosyvoice.yaml")


class CosyVoiceTts:
    """CosyVoice2 的薄封装，接口对齐 PiperTts。"""

    def __init__(
        self,
        model_dir: str | Path,
        speed: float = 1.0,
        speaker: str = DEFAULT_SPEAKER,
        ref_wav: str | Path = "",
        ref_text: str = "",
        mode: str = "auto",
        fp16: bool = False,
        volume: float = 1.0,
        fx=None,
    ):
        self.model_dir = Path(model_dir)
        self.speed = max(0.5, min(2.0, float(speed)))
        self.speaker = str(speaker or DEFAULT_SPEAKER)
        # 用户主动给了参考音频 = 想复刻；没给才用预置音色
        self._ref_explicit = bool(ref_wav)
        self.ref_wav = Path(ref_wav) if ref_wav else DEFAULT_REF_WAV
        self.ref_text = (ref_text or DEFAULT_REF_TEXT).strip()
        self.mode = (mode or "auto").lower()  # auto / sft / zero_shot
        self.fp16 = bool(fp16)
        self.volume = max(0.0, min(1.5, float(volume)))
        self.fx = fx
        self.error = ""
        self._model = None
        self._sample_rate = 24000

    # ---------- 可用性 ----------

    @property
    def _is_v2(self) -> bool:
        """看配置文件名判断版本：cosyvoice2.yaml 是 2.0，cosyvoice.yaml 是 1.0。"""
        return (self.model_dir / "cosyvoice2.yaml").exists()

    @property
    def available(self) -> bool:
        if not self.model_dir.is_dir():
            self.error = f"CosyVoice 模型目录不存在：{self.model_dir}"
            return False
        required = REQUIRED_V2 if self._is_v2 else REQUIRED_V1
        missing = [name for name in required if not (self.model_dir / name).exists()]
        if missing:
            self.error = f"{self.model_dir.name} 里缺文件：{'、'.join(missing)}（可能没下完）"
            return False
        _ensure_vendor_path()
        if not (VENDOR / "cosyvoice" / "cli" / "cosyvoice.py").exists():
            self.error = (
                f"没找到官方 CosyVoice 代码：{VENDOR}\n"
                "（PyPI 的 cosyvoice 包不是推理库，需要 git clone 官方仓库到 .vendor/CosyVoice）"
            )
            return False
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice, CosyVoice2  # noqa: F401
            import torch  # noqa: F401
        except Exception as exc:
            self.error = f"缺少依赖：{exc}"
            return False
        return True

    # ---------- 合成 ----------

    def _ensure(self):
        if self._model is not None:
            return self._model
        _ensure_vendor_path()
        from cosyvoice.cli.cosyvoice import CosyVoice, CosyVoice2

        cls = CosyVoice2 if self._is_v2 else CosyVoice
        self._model = cls(
            str(self.model_dir),
            load_jit=False,
            load_trt=False,
            fp16=self.fp16,
        )
        # llm.pt 里是 bf16 权重，而前端喂进来的 embedding 是 fp32，
        # 直接跑会报 "mat1 and mat2 must have the same dtype"。统一成 fp32。
        if self._is_v2:
            try:
                self._model.model.llm.float()
            except Exception:
                pass
        self._sample_rate = int(getattr(self._model, "sample_rate", 24000) or 24000)
        return self._model

    def _use_zeroshot(self) -> bool:
        """有内置音色就 sft；没有（CosyVoice2 就是没有）就零样本复刻。"""
        if self.mode == "zero_shot":
            return True
        if self.mode == "sft":
            return False
        # auto：显式给了参考音频就复刻，否则看模型有没有这个预置音色
        if self._ref_explicit and self.ref_wav.exists():
            return True
        speakers = getattr(getattr(self._model, "frontend", None), "spk2info", {}) or {}
        return str(self.speaker) not in speakers

    def synthesize(self, text: str, speaker: str | None = None):
        """返回 (float32 波形, 采样率)；失败返回 (None, 0)。"""
        import numpy as np

        spoken = (text or "").strip()
        if not spoken:
            return None, 0
        try:
            model = self._ensure()
            voice = str(speaker or self.speaker)
            if self._use_zeroshot() and not self.ref_wav.exists():
                raise RuntimeError(f"零样本复刻需要参考音频，但找不到：{self.ref_wav}")
            pieces = []
            if self._use_zeroshot():
                chunks = model.inference_zero_shot(
                    spoken, self.ref_text, str(self.ref_wav), stream=False, speed=self.speed
                )
            else:
                chunks = model.inference_sft(
                    spoken, voice, stream=False, speed=self.speed
                )
            for chunk in chunks:
                speech = chunk.get("tts_speech")
                if speech is None:
                    continue
                pieces.append(speech.reshape(-1).cpu().numpy())
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
        except Exception as exc:  # 模型崩了也别把播报线程带走
            self.error = f"CosyVoice 合成失败：{type(exc).__name__}: {exc}"
            return None, 0
