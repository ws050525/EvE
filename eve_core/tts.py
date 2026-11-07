"""语音播报：把回复念出来。

离线方案，用 sherpa-onnx 的 VITS 中文模型。选它而不是在线音色的原因：
edge-tts 走微软免费端点，实测 403 被挡；本地合成稳定、不联网、不花钱。

除了选嗓子，这里还有一层「AI 音色」后处理（`apply_voice_fx`）：
轻压缩 + 提亮 + 一点空间感。科幻片里的 AI 助手那种近耳、冷静的质感，
很大程度来自录音与处理，而不只是嗓音本身。

这里只管"文本 → 声音 → 播出去"，语速、打断由上层控制。
"""

from __future__ import annotations

import math
import re
import time
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


_CLAUSE = re.compile(r"[^。！？；，、：!?;,:\n…]+[。！？；，、：!?;,:\n…]?")
MIN_TAIL = 5  # 尾巴太短的片段并回上一块，否则最后一个字容易被念秃


def split_sentences(text: str, max_chars: int = 34) -> list[str]:
    """把一段话切成适合逐句合成的片段。

    按**分句**切（标点处），再贪心合并到不超过 max_chars。
    绝不在词中间下刀——之前按字数硬切，切在词里就会听到半句。
    34 字是量出来的：再长第一句就要等 2 秒以上才出声。
    """
    text = str(text or "").strip()
    if not text:
        return []

    clauses = [m.group(0).strip() for m in _CLAUSE.finditer(text)]
    clauses = [clause for clause in clauses if clause]
    if not clauses:
        return []

    chunks: list[str] = []
    buffer = ""
    for clause in clauses:
        if len(clause) > max_chars:
            # 一整句没有标点、又超长：只能硬切，这已经是最后手段
            if buffer:
                chunks.append(buffer)
                buffer = ""
            while len(clause) > max_chars:
                chunks.append(clause[:max_chars])
                clause = clause[max_chars:]
            buffer = clause
            continue

        if not buffer:
            buffer = clause
        elif len(buffer) + len(clause) <= max_chars:
            buffer += clause
        else:
            chunks.append(buffer)
            buffer = clause

    if buffer:
        chunks.append(buffer)

    # 尾巴太短就并回上一块，避免最后一块只剩两三个字
    if len(chunks) > 1 and len(chunks[-1]) < MIN_TAIL:
        chunks[-2] += chunks[-1]
        chunks.pop()

    return [chunk for chunk in chunks if chunk.strip()]


@dataclass
class TtsSettings:
    model: str = "models/voice/vits-zh-hf-eula"
    speed: float = 1.0
    speaker: int = 0
    enabled: bool = True
    volume: float = 1.0
    # VITS 的三个表现力参数：
    # noise_scale 越大语气起伏越明显（太高会沙哑），noise_scale_w 越大停顿长短越随机
    noise_scale: float = 0.667
    noise_scale_w: float = 0.8
    fx: bool = True


# ---------- 「AI 音色」后处理 ----------
#
# 三条链，都很轻，加起来只是让声音更"贴耳、稳、清楚"：
#   1. 低切一点浊音、给中高频加存在感 → 吐字更清楚
#   2. 轻压缩 → 音量起伏收窄，听感更"稳"，像录音棚里出来的
#   3. 很短的混响 → 有空间感但不糊，接近"就在你耳边"的效果


@dataclass
class VoiceFx:
    enabled: bool = True
    warmth: float = 0.12  # 低频厚度（0–0.3）
    clarity: float = 0.30  # 中高频存在感（0–0.6）
    space: float = 0.10  # 空间感混入比例（0–0.3）
    compression: float = 0.6  # 压缩强度（0–1）


def _lowpass(x, rate: int, cutoff: float):
    """一阶低通（用 FIR 近似单极点），够用且不改相位太多。"""
    import numpy as np

    alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / rate)
    n = max(8, int(rate / max(cutoff, 40.0)))
    kernel = alpha * (1.0 - alpha) ** np.arange(n)
    kernel = kernel / (kernel.sum() + 1e-12)
    return np.convolve(x, kernel, mode="same")


def _compress(x, rate: int, amount: float):
    """软压缩：超过门限的部分按比例压，整体再补一点增益。"""
    import numpy as np

    amount = max(0.0, min(1.0, amount))
    if amount <= 0:
        return x

    envelope = _lowpass(np.abs(x), rate, 60.0)
    threshold = 0.12
    ratio = 1.0 + 3.0 * amount
    gain = np.ones_like(envelope)
    loud = envelope > threshold
    gain[loud] = (threshold / envelope[loud]) ** (1.0 - 1.0 / ratio)
    return x * gain


def _reverb(x, rate: int, mix: float):
    """短混响：合成的指数衰减噪声当脉冲响应，FFT 卷积。"""
    import numpy as np

    if mix <= 0:
        return x
    length = int(rate * 0.11)
    if length < 16:
        return x
    rng = np.random.default_rng(7)  # 固定种子，保证每次听感一致
    ir = rng.standard_normal(length) * np.exp(-7.0 * np.arange(length) / length)
    ir = _lowpass(ir, rate, 3800.0)
    ir = ir / (np.abs(ir).sum() + 1e-9)

    size = len(x) + len(ir) - 1
    spectrum = np.fft.rfft(x, size) * np.fft.rfft(ir, size)
    wet = np.fft.irfft(spectrum, size)[: len(x)]
    return (1.0 - mix) * x + mix * wet


def _edge_fade(samples, rate: int, ms: float = 8.0):
    """首尾各做几毫秒淡入淡出。

    逐句播放时，每块都从非零波形突然开始/结束会有一声"咔"，
    听感上很像被掐断。很轻的淡入淡出就够。
    """
    import numpy as np

    y = np.asarray(samples, dtype="float32")
    count = int(rate * ms / 1000.0)
    if count <= 1 or len(y) < count * 2:
        return y
    ramp = np.linspace(0.0, 1.0, count, dtype="float32")
    y = y.copy()
    y[:count] *= ramp
    y[-count:] *= ramp[::-1]
    return y


def apply_voice_fx(samples, rate: int, fx: VoiceFx | None = None):
    """把合成出来的干声过一遍「AI 音色」处理。"""
    import numpy as np

    fx = fx or VoiceFx()
    if not fx.enabled or samples is None or len(samples) == 0:
        return samples

    y = np.asarray(samples, dtype="float32")
    low = _lowpass(y, rate, 220.0)
    y = y + fx.warmth * low

    high = y - _lowpass(y, rate, 3200.0)
    y = y + fx.clarity * high

    y = _compress(y, rate, fx.compression)
    y = _reverb(y, rate, fx.space)

    peak = float(np.abs(y).max())
    if peak > 0.98:
        y = y * (0.98 / peak)
    return np.clip(y, -1.0, 1.0).astype("float32")


class PiperTts:
    """sherpa-onnx VITS 合成 + sounddevice 播放。

    兼容两种模型：
    - 带 `lexicon.txt` 的中文模型（aishell3 这类，可能有 `dict/` 词典）；
    - 只给 `espeak-ng-data` 的 piper 模型（需要构建时带 espeak 前端，不一定有）。
    """

    def __init__(
        self,
        model_dir: str | Path,
        speed: float = 1.0,
        speaker: int = 0,
        threads: int = 2,
        volume: float = 1.0,
        noise_scale: float = 0.667,
        noise_scale_w: float = 0.8,
        fx: VoiceFx | None = None,
    ):
        self.model_dir = Path(model_dir)
        self.speed = max(0.5, min(2.0, float(speed)))
        self.speaker = max(0, int(speaker))
        self.threads = threads
        self.volume = max(0.0, min(1.5, float(volume)))
        self.noise_scale = max(0.0, min(1.5, float(noise_scale)))
        self.noise_scale_w = max(0.0, min(1.5, float(noise_scale_w)))
        self.fx = fx if fx is not None else VoiceFx()
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
        # 只有 VITS 能这么加载。指到 ASR / KWS 模型上会直接在 C++ 层退出进程，
        # 连异常都抓不到，所以先按文件特征挡一下。
        looks_vits = (
            (self.model_dir / "lexicon.txt").exists()
            or (self.model_dir / "espeak-ng-data").is_dir()
            or self.model_dir.name.startswith("vits")
        )
        if not looks_vits:
            self.error = f"{self.model_dir.name} 看起来不是语音合成（VITS）模型"
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
                        noise_scale=self.noise_scale,
                        noise_scale_w=self.noise_scale_w,
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
        rate = int(audio.sample_rate)
        if self.fx is not None and self.fx.enabled:
            samples = apply_voice_fx(samples, rate, self.fx)

        # 不同模型的输出电平差很多（实测 melo 只有 0.15，theresa 有 0.69），
        # 不归一化的话换音色会忽大忽小。
        peak = float(np.abs(samples).max())
        if peak > 1e-4:
            samples = samples * (0.85 / peak)

        if self.volume != 1.0:
            samples = np.clip(samples * self.volume, -1.0, 1.0)
        return np.clip(_edge_fade(samples, rate), -1.0, 1.0), rate

    def speak(self, text: str) -> bool:
        """念一段话（阻塞到播完）。调用方负责放在后台线程。

        按句子切开、边合成边播：第一句合完就开始出声，后面几句在这句播完前
        已经合成好了。整段合成再播的话，65 字的回复要先干等 3.6 秒——那才是
        "不利索"的真正原因，不是语速慢。
        """
        import queue
        import threading

        import sounddevice as sd

        sentences = split_sentences(clean_for_speech(text))
        if not sentences:
            return False

        ready: queue.Queue = queue.Queue()

        def producer() -> None:
            for sentence in sentences:
                samples, rate = self.synthesize(sentence)
                if samples is not None:
                    ready.put((samples, rate))
            ready.put(None)

        threading.Thread(target=producer, daemon=True).start()

        played = False
        while True:
            item = ready.get()
            if item is None:
                break
            samples, rate = item
            sd.play(samples, rate)
            sd.wait()
            played = True
            time.sleep(0.06)  # 句间小停顿，不然像机器人一口气读完
        return played


def list_voice_models(voice_dir: str | Path) -> list[dict]:
    """扫出 models/voice 下可用的音色。

    只认 `vits*`（不然会把 ASR/KWS 也列进来），并且要求有 `lexicon.txt`：
    没有词典的 piper 模型依赖 espeak 前端，这版 sherpa-onnx 没编进去，选了也用不了。
    """
    root = Path(voice_dir)
    if not root.is_dir():
        return []

    found: list[dict] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not path.name.startswith("vits"):
            continue
        if not any(p.suffix == ".onnx" for p in path.iterdir()):
            continue
        if not (path / "tokens.txt").exists():
            continue
        if not (path / "lexicon.txt").exists():
            continue
        found.append(
            {
                "id": path.name,
                "label": _voice_label(path.name),
                "path": str(path),
            }
        )
    return found


_VOICE_NAMES = {
    "vits-melo-tts-zh_en": "Melo（自然播报，44.1kHz）",
    "vits-zh-hf-eula": "优菈（清冷）",
    "vits-zh-hf-theresa": "德丽莎（偏软）",
    "vits-zh-hf-keqing": "刻晴（清亮）",
    "vits-zh-hf-fanchen-C": "播音腔（端正）",
    "vits-zh-aishell3": "多说话人（8kHz 电话音）",
    "vits-piper-zh_CN-huayan-medium": "Huayan（piper）",
}


def _voice_label(model_id: str) -> str:
    return _VOICE_NAMES.get(model_id, model_id)


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
