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
    asr_model: str = "models/voice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
    # 指定的模型还没下载时按顺序退：第一个存在的就用它，不会因为缺模型直接不能说话
    asr_fallbacks: tuple[str, ...] = (
        "models/voice/sherpa-onnx-paraformer-zh-small-2024-03-09",
    )
    kws_model: str = "models/voice/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
    wake_word: str = "伊芙"
    silence_ms: int = 900
    max_seconds: float = 20.0
    kws_enabled: bool = True  # 常听唤醒：不用按键，说「伊芙」就进录音
    # ---------- 唤醒与断句的阈值（调这几个最见效）----------
    # 唤醒灵敏度：调低更容易叫醒，但更容易被别的话误触发（0.05–0.4）
    kws_threshold: float = 0.22
    # 断句：低于这个音量不算说话；环境吵就调高
    vad_energy_floor: float = 0.010
    # 门限 = max(energy_floor, 环境噪音 × noise_gain)，越大越难触发
    vad_noise_gain: float = 2.5
    # 已经在说话时门限降到这个比例，避免句尾换气被当成"说完了"把句子切两半
    vad_hold_ratio: float = 0.7
    # 连续说多久才算"真的有人在说话"，调高能挡掉咳嗽、敲键盘
    vad_min_speech_ms: int = 240
    # 断句用什么：energy（默认，纯能量）/ silero（模型，嘈杂环境稳）
    vad_engine: str = "energy"
    silero_vad_model: str = "models/voice/silero_vad.onnx"
    # 播报
    tts_model: str = "models/voice/vits-zh-hf-eula"
    tts_speed: float = 1.0
    tts_speaker: int = 0
    tts_enabled: bool = True
    tts_volume: float = 1.0
    tts_noise_scale: float = 0.667  # 语气起伏，越大越"有感情"，太高会沙哑
    tts_noise_scale_w: float = 0.8  # 停顿长短的随机性
    tts_fx: bool = True  # 「AI 音色」后处理（柔和优先，别硬）
    # 后处理的五个旋钮：想更柔就往下调 clarity / compression，往上调 deharsh
    tts_fx_warmth: float = 0.16  # 低频厚度
    tts_fx_clarity: float = 0.06  # 中高频存在感（大了发燥）
    tts_fx_deharsh: float = 0.22  # 4.5kHz 以上柔化（压齿音）
    tts_fx_space: float = 0.06  # 空间感
    tts_fx_compression: float = 0.30  # 压缩（大了"压扁"、发硬）

    def audio_config(self) -> "AudioConfig":
        return AudioConfig(
            silence_ms=self.silence_ms,
            max_seconds=self.max_seconds,
            energy_floor=self.vad_energy_floor,
            noise_gain=self.vad_noise_gain,
            hold_ratio=self.vad_hold_ratio,
            min_speech_ms=self.vad_min_speech_ms,
            vad_engine=self.vad_engine,
            silero_model=self.silero_vad_model,
        )

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
    hold_ratio: float = 0.7
    # 录音前后各补一点静音：模型在"干净的开头/结尾"上认得最准，
    # 直接把话筒第一个字喂进去，开头常被吞掉
    lead_pad_ms: int = 150
    tail_pad_ms: int = 150
    vad_engine: str = "energy"
    silero_model: str = ""

    @property
    def block_size(self) -> int:
        return max(1, int(self.sample_rate * self.block_ms / 1000))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


class EnergyVad:
    """能量门限 + 静音计时的断句器。

    用**两个**门限，不是一个：

    - 开口看高门限（环境噪音 × gain）。环境再吵，也得比背景高出一截才算说话。
    - 已经开口后看低门限（高门限 × hold_ratio）。说话中途换气、句尾音量掉下去时，
      用高门限会当场判定"说完了"，一句话就被拦腰切断——这是断句最隐蔽的坑。

    环境噪音用前 8 块（≈256ms）的**中位数**估，不用最大值：最大值会被开头
    一声咳嗽、一次敲桌子顶到很高，之后整段都判不出说话（"没听到说话"）。
    没在说话的时候再慢慢跟住环境，空调开了、风扇转了也能跟上。
    """

    CALIBRATE_BLOCKS = 8  # 前 8 个块（≈256ms）用来估环境噪音
    # 噪声估计的上限。万一用户一按下就开始说话，那 256ms 录到的是人声不是环境，
    # 不加盖会把门限顶到再也听不见人说话。0.05 已经是很吵的房间了。
    NOISE_CEILING = 0.05

    def __init__(self, cfg: AudioConfig):
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self._calib: list[float] = []
        self._noise = 0.0
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._in_speech = False

    @property
    def heard_speech(self) -> bool:
        return self._speech_ms >= self.cfg.min_speech_ms

    @property
    def calibrated(self) -> bool:
        return len(self._calib) >= self.CALIBRATE_BLOCKS

    def _start_level(self) -> float:
        return max(self.cfg.energy_floor, self._noise * self.cfg.noise_gain)

    def threshold(self) -> float:
        """当前生效的门限：没开口看高门限，开口了看低门限。"""
        start = self._start_level()
        if self._in_speech:
            return max(self.cfg.energy_floor * 0.6, start * self.cfg.hold_ratio)
        return start

    def update(self, rms: float) -> bool:
        """喂一个音频块的 RMS，返回 True 表示"这句说完了"。"""
        level = float(rms)

        if not self.calibrated:
            self._calib.append(level)
            self._noise = min(_median(self._calib), self.NOISE_CEILING)
            return False

        if self._in_speech:
            if level >= self.threshold():
                self._silence_ms = 0.0
                self._speech_ms += self.cfg.block_ms
                return False
            self._silence_ms += self.cfg.block_ms
            if self._silence_ms >= self.cfg.silence_ms:
                self._in_speech = False
                # 收尾就说收尾，哪怕这几下响动不够 min_speech_ms。
                # 调用方看 heard_speech 决定这句算不算数——不返回 True 的话，
                # 一声咳嗽之后会一直录到 max_seconds 才停。
                return True
            return False

        if level >= self._start_level():
            self._in_speech = True
            self._speech_ms += self.cfg.block_ms
            self._silence_ms = 0.0
            return False

        # 安静时跟一下环境噪音，别一直用开头那 256ms 的印象
        self._noise = min(
            self._noise * 0.97 + level * 0.03, self.NOISE_CEILING
        )
        return False

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
        self.note = ""  # 实际用的是哪种断句，界面/日志可以看到

    @property
    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return True

    def record_utterance(self, on_level: Callable[[float], None] | None = None):
        """录一句话，静音自动收尾。返回 float32 numpy 数组；没听到人说话就返回空。

        收尾之后在前后各补一小段静音再交给识别：模型在干净的头尾上认得更准，
        直接怼一个"开口第一帧"和"掐断的尾巴"进去，开头结尾最容易丢字。
        """
        import numpy as np
        import sounddevice as sd

        cfg = self.cfg
        limit_frames = int(cfg.max_seconds * cfg.sample_rate)

        with sd.InputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=cfg.block_size,
        ) as stream:
            blocks, heard = self._collect(stream, cfg, limit_frames, on_level)

        if not blocks or not heard:
            return np.zeros(0, dtype="float32")

        audio = np.concatenate(blocks).astype("float32")
        lead = np.zeros(
            max(0, int(cfg.sample_rate * cfg.lead_pad_ms / 1000)), dtype="float32"
        )
        tail = np.zeros(
            max(0, int(cfg.sample_rate * cfg.tail_pad_ms / 1000)), dtype="float32"
        )
        return np.concatenate([lead, audio, tail]).astype("float32")

    # ---------- 采集循环 ----------

    def _read_block(self, stream, cfg: AudioConfig):
        data = stream.read(cfg.block_size)[0]
        samples = data[:, 0] if getattr(data, "ndim", 1) > 1 else data
        return samples

    def _collect(self, stream, cfg: AudioConfig, limit_frames: int, on_level):
        blocks: list = []
        total = 0

        silero = None
        if cfg.vad_engine == "silero":
            silero = _make_silero(cfg)
            if silero is None:
                self.note = "silero 模型不可用，回退到能量断句"
            else:
                self.note = "silero 断句"

        if silero is not None:
            heard = False
            try:
                while total < limit_frames:
                    samples = self._read_block(stream, cfg)
                    blocks.append(samples.copy())
                    total += len(samples)
                    if on_level is not None:
                        on_level(EnergyVad.rms(samples))
                    silero.accept_waveform(samples)
                    if silero.is_speech_detected():
                        heard = True
                    elif heard:
                        break  # 说完一段，silero 判定结束
                return blocks, heard
            except Exception as exc:  # 任何毛病都退回能量断句，别让语音彻底不能用
                self.note = f"silero 出错({type(exc).__name__})，回退到能量断句"
                blocks.clear()
                total = 0

        if cfg.vad_engine != "silero":
            self.note = "能量断句"

        vad = EnergyVad(cfg)
        while total < limit_frames:
            samples = self._read_block(stream, cfg)
            blocks.append(samples.copy())
            total += len(samples)
            level = EnergyVad.rms(samples)
            if on_level is not None:
                on_level(level)
            if vad.update(level):
                break
        return blocks, vad.heard_speech


def _make_silero(cfg: AudioConfig):
    """按配置造一个 sherpa-onnx 的 silero VAD；不可用返回 None。

    能量断句在安静房间够用，一有风扇、空调、键盘就难调；silero 是模型判定，
    嘈杂环境下稳得多。它是加分项，坏了就回退，不能让语音整体不可用。
    """
    if not cfg.silero_model:
        return None
    model = Path(cfg.silero_model)
    if not model.exists():
        return None
    try:
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model)
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = max(0.25, cfg.silence_ms / 1000)
        config.silero_vad.min_speech_duration = max(0.1, cfg.min_speech_ms / 1000)
        config.silero_vad.window_size = 512
        config.sample_rate = cfg.sample_rate
        return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
    except Exception:
        return None


# ---------- 识别 ----------

# sherpa-onnx 的离线识别器家族。同一份 samples 喂进去，只有加载方式不同，
# 所以按模型目录里的文件特征自动认，换模型只改 config.toml，不用改代码。
ASR_FAMILIES = ("sense_voice", "paraformer", "whisper", "transducer")


def detect_asr_family(model_dir: str | Path) -> str:
    """看目录里的文件（和目录名）猜这是哪一族模型；认不出返回空串。"""
    root = Path(model_dir)
    if not root.is_dir():
        return ""
    name = root.name.lower()
    files = {p.name.lower() for p in root.glob("*.onnx")}

    if "sense-voice" in name or "sense_voice" in name:
        return "sense_voice"
    if "paraformer" in name:
        return "paraformer"
    if "whisper" in name:
        return "whisper"

    # 只有权重不够，还要看结构：paraformer / sensevoice 都是单个 model*.onnx
    if files & {"model.onnx", "model.int8.onnx"}:
        return "paraformer"
    if any(f.startswith("encoder") for f in files) and any(
        f.startswith("joiner") for f in files
    ):
        return "transducer"
    if any(f.startswith("encoder") for f in files) and any(
        f.startswith("decoder") for f in files
    ):
        return "whisper"
    return ""


def _pick_weights(root: Path, prefixes: tuple[str, ...]) -> Path | None:
    """在目录里挑权重：优先非 int8（准），只有 int8 就用它（快）。"""
    found: list[Path] = []
    for prefix in prefixes:
        found = [p for p in sorted(root.glob(f"{prefix}*.onnx")) if p.is_file()]
        if found:
            break
    if not found:
        return None
    for path in found:
        if "int8" not in path.name:
            return path
    return found[0]


class SherpaRecognizer:
    """sherpa-onnx 离线识别。

    支持 SenseVoice / paraformer / whisper / transducer 四族，按目录自动认
    （见 `detect_asr_family`）。首选 SenseVoice：中文准确率比 paraformer-small
    高一截，还自带标点和数字规整，认出来的文字直接能看。
    """

    def __init__(self, model_dir: str | Path, fallbacks: tuple[str, ...] | list[str] = ()):
        self.requested = Path(model_dir)
        candidates = [self.requested, *(Path(item) for item in fallbacks)]
        self.model_dir = next((path for path in candidates if path.is_dir()), self.requested)
        self.note = ""
        if self.model_dir != self.requested:
            self.note = f"用回退模型 {self.model_dir.name}（{self.requested.name} 还没下载）"
        self._recognizer = None
        self.error = ""

    @property
    def family(self) -> str:
        return detect_asr_family(self.model_dir)

    @property
    def available(self) -> bool:
        if not self.model_dir.is_dir():
            self.error = f"模型目录不存在：{self.model_dir}"
            return False
        if not (self.model_dir / "tokens.txt").exists():
            self.error = f"{self.model_dir} 里缺 tokens.txt"
            return False
        if not self.family:
            self.error = f"认不出 {self.model_dir.name} 是哪一族识别模型"
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

        tokens = str(self.model_dir / "tokens.txt")
        family = self.family

        if family == "sense_voice":
            model = _pick_weights(self.model_dir, ("model",))
            if model is None:
                raise FileNotFoundError(f"{self.model_dir} 里找不到 model*.onnx")
            # use_itn 打开才带标点和数字规整；老版本 sherpa-onnx 没有这些参数，退回去
            try:
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model),
                    tokens=tokens,
                    num_threads=2,
                    use_itn=True,
                    language="zh",
                )
            except TypeError:
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model), tokens=tokens, num_threads=2
                )
        elif family == "paraformer":
            model = _pick_weights(self.model_dir, ("model",))
            if model is None:
                raise FileNotFoundError(f"{self.model_dir} 里找不到 model*.onnx")
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=str(model),
                tokens=tokens,
                num_threads=2,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
            )
        elif family == "whisper":
            encoder = _pick_weights(self.model_dir, ("encoder",))
            decoder = _pick_weights(self.model_dir, ("decoder",))
            if encoder is None or decoder is None:
                raise FileNotFoundError(f"{self.model_dir} 里缺 encoder / decoder")
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=str(encoder),
                decoder=str(decoder),
                tokens=tokens,
                num_threads=2,
                language="zh",
            )
        else:  # transducer
            encoder = _pick_weights(self.model_dir, ("encoder",))
            decoder = _pick_weights(self.model_dir, ("decoder",))
            joiner = _pick_weights(self.model_dir, ("joiner",))
            if encoder is None or decoder is None or joiner is None:
                raise FileNotFoundError(f"{self.model_dir} 里缺 encoder/decoder/joiner")
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(encoder),
                decoder=str(decoder),
                joiner=str(joiner),
                tokens=tokens,
                num_threads=2,
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
