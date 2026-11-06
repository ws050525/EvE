"""语音播报：把回复念出来。

离线方案，用 sherpa-onnx 的 VITS（piper 中文女声 zh_CN-huayan-medium）。
选它而不是在线音色的原因：edge-tts 走微软免费端点，实测 403 被挡；
本地合成的音质虽然不如神经网络云端音色，但稳定、不联网、不花钱。

这里只管"文本 → 声音 → 播出去"，语速、打断由上层控制。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 念出来没意义的符号：markdown 标记、代码块、颜文字、表情
_MD = re.compile(r"[*_`#>|]+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_KAOMOJI = re.compile(r"[（(][^（()）]{0,12}[・ω´`°▽人∀≧≦▔▽][^（()）]{0,12}[)）]")
_EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf]")
_SPACES = re.compile(r"[ \t]+")
# 模型词典里没有的装饰符号，留着只会刷 OOV 警告
_DECOR = re.compile(r"[～~【】「」『』《》※•·^|<>]")

MAX_SPEECH_CHARS = 220


def clean_for_speech(text: str) -> str:
    """把回复整理成适合朗读的句子。

    回复里常有 markdown 符号、代码块、颜文字，直接念会变成"星号星号"。
    """
    text = str(text or "")
    text = text.replace("\r", "\n")
    # 代码块整段丢掉：念代码没有意义，只会拖长播报
    text = re.sub(r"```.*?```", "，", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _KAOMOJI.sub("", text)
    text = _EMOJI.sub("", text)
    text = _MD.sub("", text)
    # 列表符号：不删的话会念成"减号打开了记事本"
    text = re.sub(r"(?m)^\s*[-•·]\s*", "", text)
    text = re.sub(r"(?m)^\s*\d+[.、)]\s*", "", text)
    text = _DECOR.sub("", text)
    text = re.sub(r"[（(]\s*[)）]", "", text)
    text = _SPACES.sub(" ", text)
    text = re.sub(r"\n+", "。", text)
    text = re.sub(r"。{2,}", "。", text)
    text = re.sub(r"[，。]{2,}", "，", text)
    text = text.strip("，。 ")

    if len(text) > MAX_SPEECH_CHARS:
        cut = text[:MAX_SPEECH_CHARS]
        # 尽量在标点处断开，别把词切一半
        for mark in ("。", "！", "？", "，", "；"):
            index = cut.rfind(mark)
            if index >= MAX_SPEECH_CHARS // 2:
                cut = cut[: index + 1]
                break
        text = cut.rstrip("，、 ")
    return text


@dataclass
class TtsSettings:
    model: str = "models/voice/vits-zh-aishell3"
    speed: float = 1.0
    speaker: int = 0
    enabled: bool = True


class PiperTts:
    """sherpa-onnx VITS 合成 + sounddevice 播放。

    兼容两种模型：
    - 带 `lexicon.txt` 的中文模型（aishell3 这类，可能有 `dict/` 词典）；
    - 只给 `espeak-ng-data` 的 piper 模型（需要构建时带 espeak 前端，不一定有）。
    """

    def __init__(
        self, model_dir: str | Path, speed: float = 1.0, speaker: int = 0, threads: int = 2
    ):
        self.model_dir = Path(model_dir)
        self.speed = max(0.5, min(2.0, float(speed)))
        self.speaker = max(0, int(speaker))
        self.threads = threads
        self.error = ""
        self._tts = None

    @property
    def available(self) -> bool:
        if not self.model_dir.is_dir():
            self.error = f"TTS 模型目录不存在：{self.model_dir}"
            return False
        if not (self.model_dir / "tokens.txt").exists():
            self.error = f"{self.model_dir} 里缺 tokens.txt"
            return False
        try:
            import sherpa_onnx  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception as exc:
            self.error = f"缺少依赖：{exc}"
            return False
        return True

    def _weights(self) -> Path:
        candidates = [
            p
            for p in sorted(self.model_dir.glob("*.onnx"))
            if p.is_file()
        ]
        if not candidates:
            raise FileNotFoundError(f"{self.model_dir} 里找不到 onnx 模型")
        # 优先非量化版本，音质更好；只有 int8 就用它
        for path in candidates:
            if "int8" not in path.name:
                return path
        return candidates[0]

    def _ensure(self):
        if self._tts is not None:
            return self._tts
        import sherpa_onnx

        root = self.model_dir
        lexicon = root / "lexicon.txt"
        data_dir = root / "espeak-ng-data"
        dict_dir = root / "dict"

        self._tts = sherpa_onnx.OfflineTts(
            sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(self._weights()),
                        lexicon=str(lexicon) if lexicon.exists() else "",
                        tokens=str(root / "tokens.txt"),
                        data_dir=str(data_dir) if data_dir.is_dir() else "",
                        dict_dir=str(dict_dir) if dict_dir.is_dir() else "",
                    ),
                    num_threads=self.threads,
                ),
                max_num_sentences=1,
            )
        )
        return self._tts

    def synthesize(self, text: str, speaker: int | None = None):
        """返回 (samples, sample_rate)；文本为空或失败返回 (None, 0)。"""
        import numpy as np

        spoken = clean_for_speech(text)
        if not spoken:
            return None, 0
        tts = self._ensure()
        sid = self.speaker if speaker is None else max(0, int(speaker))
        audio = tts.generate(spoken, sid=sid, speed=self.speed)
        samples = np.asarray(audio.samples, dtype="float32")
        if samples.size == 0:
            return None, 0
        return samples, int(audio.sample_rate)

    def speak(self, text: str) -> bool:
        """合成并播完（阻塞）。调用方负责放在后台线程。"""
        import sounddevice as sd

        samples, rate = self.synthesize(text)
        if samples is None:
            return False
        sd.play(samples, rate)
        sd.wait()
        return True


class SapiTts:
    """兜底：Windows 自带 SAPI（pyttsx3）。音色机械，但不需要任何模型，一定能出声。"""

    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self.error = ""
        self._engine = None

    @property
    def available(self) -> bool:
        try:
            import pyttsx3  # noqa: F401
        except Exception as exc:
            self.error = f"没装 pyttsx3：{exc}"
            return False
        return True

    def _ensure(self):
        if self._engine is None:
            import pyttsx3

            engine = pyttsx3.init()
            for voice in engine.getProperty("voices"):
                if "Chinese" in voice.name:
                    engine.setProperty("voice", voice.id)
                    break
            engine.setProperty("rate", int(170 * self.speed))
            self._engine = engine
        return self._engine

    def speak(self, text: str) -> bool:
        spoken = clean_for_speech(text)
        if not spoken:
            return False
        engine = self._ensure()
        engine.say(spoken)
        engine.runAndWait()
        return True
