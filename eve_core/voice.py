"""语音输入：收音、断句、识别、唤醒词。

分四层，每层都能单独替换，也都能单独测：

1. `Recorder`   —— 麦克风 → float32 单声道。没装 sounddevice 就 `available=False`。
2. `EnergyVad`  —— "是不是有人在说话"，纯状态机，不碰音频库，所以能单测。
3. `Recognizer` —— sherpa-onnx 离线识别。没有模型就 `available=False`，上层给提示。
4. `match_wake` —— 唤醒词判定，纯文本匹配。

唤醒词先说清楚：现在是**文本级**的——按键说完一句，检查开头是不是「伊芙」，
是就剥掉再交给 agent。常听 KWS（不用按键）是下一层，模型已经在下了；
这一层做成纯函数，换 KWS 时判定逻辑还能复用。

为什么不用 whisper / webrtcvad：
- `openai-whisper` 要求 numpy ≤ 1.26，这里是 2.4.6，导入就炸；
- `webrtcvad` 缺 pkg_resources。
sherpa-onnx 只依赖 onnxruntime，已经装好了，而且 KWS / ASR / TTS 共用同一套运行时。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SAMPLE_RATE = 16000

# 「伊芙」的常见误识别。ASR 对两字中文词的容错就这么点，宁可多写几个变体
WAKE_VARIANTS = (
    "伊芙",
    "伊弗",
    "衣芙",
    "伊夫",
    "衣服",  # 实测 paraformer 把「伊芙」听成这个，必须收
    "eve",
    "小伊",
)

_PUNCT = re.compile(r"[\s，。！？、,.!?；;：:\"'“”‘’()（）\[\]【】…—\-]+")
_LEAD = re.compile(r"^[\s，。！？、,.!?；;：:\"'“”‘’()（）\[\]【】…—\-]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _PUNCT.sub("", text)


def wake_variants(word: str = "伊芙") -> tuple[str, ...]:
    word = normalize(word)
    variants = {word}
    variants.update(normalize(v) for v in WAKE_VARIANTS)
    return tuple(sorted(v for v in variants if v))


def match_wake(text: str, word: str = "伊芙") -> tuple[bool, str]:
    """返回 (是否唤醒, 去掉唤醒词后的指令)。

    只认「开头是唤醒词」——句子中间出现不算，否则聊天里提一句就误触发。
    """
    normalized = normalize(text)
    if not normalized:
        return False, ""

    for variant in sorted(wake_variants(word), key=len, reverse=True):
        if normalized.startswith(variant):
            rest = normalized[len(variant) :]
            # 恢复成原文里对应位置之后的文本，保留大小写和标点
            offset = _find_word_offset(text, variant)
            if offset >= 0:
                return True, _LEAD.sub("", text[offset:]).strip()
            return True, rest
    return False, text.strip()


def _find_word_offset(text: str, variant: str) -> int:
    """在原文里找到唤醒词结束的位置，好把后面的原话完整留下来。"""
    index = 0
    matched = 0
    while index < len(text) and matched < len(variant):
        ch = normalize(text[index])
        if not ch:
            index += 1
            continue
        if ch == variant[matched]:
            matched += 1
        index += 1
    return index if matched == len(variant) else -1


# ---------- 语音切档 ----------

MODE_SYNONYMS = {
    "chat": ("聊天", "闲聊", "陪聊"),
    "desktop": ("桌面", "操作电脑", "电脑操作"),
    "work": ("工作", "干活"),
}

_MODE_PREFIXES = ("切换到", "切到", "换到", "换成", "进入", "用", "打开")
_MODE_SUFFIXES = ("模式", "档位", "档")


def mode_aliases(labels: dict[str, str]) -> dict[str, str]:
    """别名表：归一化后的说法 → mode_id。labels 是 {mode_id: 显示名}。"""
    table: dict[str, str] = {}
    for mode_id, label in labels.items():
        for word in (label, mode_id, *MODE_SYNONYMS.get(mode_id, ())):
            key = normalize(word)
            if key:
                table[key] = mode_id
    return table


def parse_mode_command(text: str, aliases: dict[str, str], max_chars: int = 10) -> str | None:
    """「切换到工作」「桌面模式」这类指令 → mode_id；不像指令就返回 None。

    刻意做得很窄：长度限制 + 必须整句就是档位名。宁可漏判（交给模型），
    也别把「我在想工作的事」这种话当成切档。
    """
    raw = normalize(text)
    if not raw or len(raw) > max_chars:
        return None

    for prefix in _MODE_PREFIXES:
        if raw.startswith(prefix) and len(raw) > len(prefix):
            raw = raw[len(prefix) :]
            break

    changed = True
    while changed and raw:
        changed = False
        for suffix in _MODE_SUFFIXES:
            if raw.endswith(suffix) and len(raw) > len(suffix):
                raw = raw[: -len(suffix)]
                changed = True

    return aliases.get(raw)


@dataclass
class VoiceSettings:
    asr_model: str = "models/voice/sherpa-onnx-paraformer-zh-small-2024-03-09"
    kws_model: str = "models/voice/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
    wake_word: str = "伊芙"
    silence_ms: int = 900
    max_seconds: float = 20.0
    kws_enabled: bool = True  # 常听唤醒：不用按键，说「伊芙」就进录音
    # 播报
    tts_model: str = "models/voice/vits-zh-hf-eula"
    tts_speed: float = 1.0
    tts_speaker: int = 0
    tts_enabled: bool = True
    tts_volume: float = 1.0
    tts_noise_scale: float = 0.667  # 语气起伏，越大越"有感情"，太高会沙哑
    tts_noise_scale_w: float = 0.8  # 停顿长短的随机性

    def wake_tokens(self) -> tuple[str, str]:
        """唤醒词的拼音 token（KWS 模型按声母+韵母建模）。"""
        return _wake_tokens(self.wake_word)


# 唤醒词 → 拼音 token。目前只覆盖「伊芙」；换词就在这里加一行。
_WAKE_TOKENS = {
    "伊芙": "y ī f ú",
}


def _wake_tokens(word: str) -> tuple[str, str]:
    tokens = _WAKE_TOKENS.get(word)
    return (tokens, word) if tokens else ("", word)


@dataclass
class AudioConfig:
    sample_rate: int = SAMPLE_RATE
    block_ms: int = 32
    silence_ms: int = 900
    max_seconds: float = 20.0
    min_speech_ms: int = 240
    energy_floor: float = 0.010
    noise_gain: float = 2.5

    @property
    def block_size(self) -> int:
        return max(1, int(self.sample_rate * self.block_ms / 1000))


class EnergyVad:
    """能量门限 + 静音计时的断句器。

    只做一件事：什么时候该停止录音。判定只看 RMS，不依赖任何音频库，
    所以可以用一串假数字把整条状态机测干净。
    """

    CALIBRATE_BLOCKS = 8  # 前 8 个块用来估环境噪音

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self._noise = 0.0
        self._calibrated = 0
        self._speech_ms = 0.0
        self._silence_ms = 0.0

    @property
    def heard_speech(self) -> bool:
        return self._speech_ms >= self.cfg.min_speech_ms

    def threshold(self) -> float:
        return max(self.cfg.energy_floor, self._noise * self.cfg.noise_gain)

    def update(self, rms: float) -> bool:
        """喂一个音频块的 RMS，返回 True 表示"这句说完了"。"""
        if self._calibrated < self.CALIBRATE_BLOCKS:
            # 环境噪音取前几个块的最大值，避免开头有个响动就把门限抬太高
            self._noise = max(self._noise, rms)
            self._calibrated += 1

        if rms >= self.threshold():
            self._speech_ms += self.cfg.block_ms
            self._silence_ms = 0.0
        else:
            self._silence_ms += self.cfg.block_ms

        return self.heard_speech and self._silence_ms >= self.cfg.silence_ms

    @staticmethod
    def rms(samples) -> float:
        if not len(samples):
            return 0.0
        total = 0.0
        for value in samples:
            total += float(value) * float(value)
        return math.sqrt(total / len(samples))


# ---------- 收音 ----------


class Recorder:
    def __init__(self, cfg: AudioConfig | None = None):
        self.cfg = cfg or AudioConfig()

    @property
    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return True

    def record_utterance(self, on_level: Callable[[float], None] | None = None):
        """录一句话，静音自动收尾。返回 float32 numpy 数组；没听到人说话就返回空。"""
        import numpy as np
        import sounddevice as sd

        cfg = self.cfg
        vad = EnergyVad(cfg)
        blocks: list = []
        total_frames = 0
        limit_frames = int(cfg.max_seconds * cfg.sample_rate)

        with sd.InputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=cfg.block_size,
        ) as stream:
            while total_frames < limit_frames:
                data, overflowed = stream.read(cfg.block_size)
                samples = data[:, 0] if getattr(data, "ndim", 1) > 1 else data
                blocks.append(samples.copy())
                total_frames += len(samples)
                level = EnergyVad.rms(samples)
                if on_level is not None:
                    on_level(level)
                if vad.update(level):
                    break

        if not blocks or not vad.heard_speech:
            return np.zeros(0, dtype="float32")
        return np.concatenate(blocks).astype("float32")


# ---------- 识别 ----------


class SherpaRecognizer:
    """sherpa-onnx 离线识别（paraformer 中文小模型）。"""

    def __init__(self, model_dir: str | Path):
        self.model_dir = Path(model_dir)
        self._recognizer = None
        self.error = ""

    @property
    def available(self) -> bool:
        if not self.model_dir.is_dir():
            self.error = f"模型目录不存在：{self.model_dir}"
            return False
        try:
            import sherpa_onnx  # noqa: F401
        except Exception as exc:
            self.error = f"没装 sherpa-onnx：{exc}"
            return False
        return True

    def _ensure(self):
        if self._recognizer is not None:
            return self._recognizer
        import sherpa_onnx

        # 官方包有时叫 model.onnx，有时只给 model.int8.onnx（量化版，CPU 上更快）
        model = None
        for name in ("model.int8.onnx", "model.onnx"):
            candidate = self.model_dir / name
            if candidate.exists():
                model = candidate
                break
        tokens = self.model_dir / "tokens.txt"
        if model is None or not tokens.exists():
            raise FileNotFoundError(
                f"{self.model_dir} 里缺 model.onnx / model.int8.onnx / tokens.txt"
            )

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(model),
            tokens=str(tokens),
            num_threads=2,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
        )
        return self._recognizer

    def transcribe(self, samples) -> str:
        import numpy as np

        if samples is None or len(samples) == 0:
            return ""
        recognizer = self._ensure()
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.asarray(samples, dtype="float32"))
        recognizer.decode_stream(stream)
        return str(stream.result.text or "").strip()


@dataclass
class VoiceState:
    """给界面看的语音状态。"""

    listening: bool = False
    transcribing: bool = False
    last_text: str = ""
    error: str = ""
    extras: dict = field(default_factory=dict)


# ---------- 常听唤醒（KWS） ----------


class KwsDetector:
    """sherpa-onnx 关键词唤醒。

    模型是拼音建模（声母 + 韵母），所以自定义唤醒词要写成拼音 token：
    「伊芙」= `y ī f ú`。模型自带 keywords.txt 里有现成的写法可以照抄。
    """

    _WEIGHT_NAMES = (
        "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        "encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        "encoder-epoch-99-avg-1-chunk-16-left-64.int8.onnx",
        "encoder-epoch-99-avg-1-chunk-16-left-64.onnx",
    )

    def __init__(
        self,
        model_dir: str | Path,
        keywords: list[tuple[str, str]],
        threshold: float = 0.25,
        score: float = 1.0,
    ):
        self.model_dir = Path(model_dir)
        self.keywords = keywords
        self.threshold = threshold
        self.score = score
        self.error = ""
        self._spotter = None
        self._stream = None

    @property
    def available(self) -> bool:
        if not self.model_dir.is_dir():
            self.error = f"KWS 模型目录不存在：{self.model_dir}"
            return False
        try:
            import sherpa_onnx  # noqa: F401
        except Exception as exc:
            self.error = f"没装 sherpa-onnx：{exc}"
            return False
        return True

    def _weights(self) -> tuple[Path, Path, Path]:
        for name in self._WEIGHT_NAMES:
            encoder = self.model_dir / name
            if not encoder.exists():
                continue
            stem = name.replace("encoder-", "").replace(".int8.onnx", "").replace(".onnx", "")
            decoder = self.model_dir / f"decoder-{stem}.onnx"
            joiner = self.model_dir / f"joiner-{stem}.onnx"
            if decoder.exists() and joiner.exists():
                return encoder, decoder, joiner
        raise FileNotFoundError(f"{self.model_dir} 里找不到 KWS 权重")

    def _keywords_file(self) -> Path:
        path = self.model_dir / "eve_keywords.txt"
        lines = [f"{tokens} @{label}" for tokens, label in self.keywords]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _ensure(self):
        if self._spotter is not None:
            return self._spotter
        import sherpa_onnx

        encoder, decoder, joiner = self._weights()
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(self.model_dir / "tokens.txt"),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            keywords_file=str(self._keywords_file()),
            num_threads=2,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            keywords_score=self.score,
            keywords_threshold=self.threshold,
            num_trailing_blanks=1,
        )
        self._stream = self._spotter.create_stream()
        return self._spotter

    def accept(self, samples) -> str:
        """喂**一小段**音频（约 100ms），命中关键词就返回它的标签，否则空串。

        注意：必须增量喂。实测把一整段 1.4 秒的音频一次性 accept_waveform 进去，
        KWS 什么都检测不到；按 100ms 分块喂，第 7 块就命中了。
        这个模型是流式的，只喂不给它 decode 的机会等于没用。
        """
        import numpy as np

        if samples is None or len(samples) == 0:
            return ""

        spotter = self._ensure()
        self._stream.accept_waveform(SAMPLE_RATE, np.asarray(samples, dtype="float32"))

        while spotter.is_ready(self._stream):
            spotter.decode_stream(self._stream)
            try:
                result = str(spotter.get_result(self._stream) or "").strip()
            except Exception:
                result = str(getattr(self._stream, "result", "") or "").strip()
            if result:
                spotter.reset_stream(self._stream)
                return result.lstrip("@")
        return ""

    def reset(self) -> None:
        if self._spotter is not None and self._stream is not None:
            try:
                self._spotter.reset_stream(self._stream)
            except Exception:
                self._stream = self._spotter.create_stream()
