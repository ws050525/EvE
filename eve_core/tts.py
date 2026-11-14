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
import queue
import re
import threading
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

# 念多少字。以前是 220，长回复的后半段直接被砍掉——「后面的话没念」就是这么来的。
# 现在只防病态输入（几千字的整篇文档），正常回复一律念完：真正控制节奏的是
# 下面的逐句切分，不是把文本截短。
MAX_SPEECH_CHARS = 3000


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


_END_MARKS = "。！？!?；;…\n"
_SOFT_MARKS = "，、,：:"


class SentenceStreamer:
    """把流式吐出来的文字攒成「可以开念的句子」。

    逐句播报的两个坑都在切分上：

    1. 等整段说完再切，播报必然落后于文字（用户看到字已经出来一大段，声音还在后面追）；
       所以要边生成边切，切出一句就送进合成。
    2. 见标点就切，会切出「嗯。」这种两三个字的碎块。每块都要起一次合成、接一次音频，
       碎块多了听感就是断断续续，而且短块的**头尾**最容易被音频设备吞掉。

    所以这里只做一件事：攒够一个「完整、够长」的句子就吐出来，其余继续攒。
    吐出来的片段拼回去等于原文（除了首尾空白），一个字都不丢。
    """

    def __init__(self, max_chars: int = 34, min_chars: int = 6, hard_limit: int = 120):
        self.max_chars = max(8, int(max_chars))
        self.min_chars = max(2, int(min_chars))
        self.hard_limit = max(self.max_chars, int(hard_limit))
        self._buffer = ""

    def push(self, text: str) -> list[str]:
        """喂一段新文字，返回这次能开念的句子（可能为空）。"""
        self._buffer += str(text or "")
        return self._drain(final=False)

    def flush(self) -> list[str]:
        """文本结束：把剩下的全部吐出去，不留尾巴。"""
        return self._drain(final=True)

    @property
    def pending(self) -> str:
        return self._buffer

    def _drain(self, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            end = self._cut_point(final)
            if end is None:
                break
            piece, self._buffer = self._buffer[:end], self._buffer[end:]
            out.extend(split_sentences(piece, self.max_chars))
        return [chunk for chunk in out if chunk.strip()]

    def _cut_point(self, final: bool) -> int | None:
        buf = self._buffer
        if not buf:
            return None
        if final:
            return len(buf)

        # 先找句末标点：够长才算一句，太短的（「嗯。」）继续往后攒
        for index, char in enumerate(buf):
            if char in _END_MARKS and index + 1 >= self.min_chars:
                return index + 1

        # 一直没句号：退一步找逗号，免得第一句要等太久才出声
        if len(buf) >= self.max_chars:
            soft = -1
            for index, char in enumerate(buf[: self.max_chars + 1]):
                if char in _SOFT_MARKS:
                    soft = index
            if soft + 1 >= self.min_chars:
                return soft + 1

        # 连逗号都没有、已经长得离谱：只能硬切（最后手段，正常回复碰不到）
        if len(buf) >= self.hard_limit:
            return self.max_chars
        return None


def _silence(rate: int, ms: float):
    import numpy as np

    count = max(0, int(rate * ms / 1000.0))
    return np.zeros(count, dtype="float32")


class PlaybackSession:
    """一次播报：文本片段推进来，边合成边播。

    为什么不是「一句一个 sd.play」——这正是"前后几句念不全"的根因：

    - 每句都开关一次音频设备。设备刚启动那几十毫秒是吞数据的，
      句子的**开头**就没了；`sd.wait()` 一返回就换下一句，最后一点尾音
      和下一次启动撞在一起，句子的**结尾**也就没了。
    - 块越短，头尾占比越大，所以碎句听起来缺得最明显。

    现在整段只开一路流：开头垫一小段静音（等设备真正跑起来），
    句间用静音填（不欠载），收尾补一段静音并等缓冲区播完才关流。
    合成和播放分两条线程，下一句在上一句还没播完时就合成好了。
    """

    LEAD_MS = 120  # 开头垫静音：设备启动期吃掉的声音就落在这段静音里
    TAIL_MS = 180  # 结尾补静音：留出余量，尾音不会被关流掐掉
    GAP_BLOCK_MS = 20  # 等下一句时按 20ms 填静音，避免欠载的"咔"
    SENTENCE_GAP_MS = 70  # 句与句之间留一口气：贴太紧会显得急、听着"硬"

    def __init__(self, engine, on_start=None, on_done=None, on_error=None):
        self.engine = engine
        self._on_start = on_start
        self._on_done = on_done
        self._on_error = on_error
        self._cond = threading.Condition()
        self._pending: list[str] = []
        self._splitter = SentenceStreamer()
        self._audio: queue.Queue = queue.Queue()
        self._closed = False
        self._cancelled = False
        self._started = False
        self._stream = None
        self._error = ""
        self.played = False
        self._threads: list[threading.Thread] = []

    # ---------- 外部接口 ----------

    def push(self, text: str) -> None:
        """喂一段话。可以反复调用（流式场景），也可以一口气推完整段。"""
        with self._cond:
            if self._closed or self._cancelled:
                return
            self._pending.extend(self._splitter.push(text))
            self._cond.notify_all()

    def close(self) -> None:
        """文本到头了：把剩下的吐出去，播完自然结束。"""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._pending.extend(self._splitter.flush())
            self._cond.notify_all()

    def cancel(self) -> None:
        """立刻闭嘴（用户发了新消息 / 关窗口）。"""
        with self._cond:
            self._cancelled = True
            self._cond.notify_all()
        try:
            self._audio.put_nowait(None)  # 叫醒播放线程
        except Exception:
            pass
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._threads = [
            threading.Thread(target=self._produce, daemon=True, name="eve-tts-synth"),
            threading.Thread(target=self._play, daemon=True, name="eve-tts-out"),
        ]
        for thread in self._threads:
            thread.start()

    def join(self, timeout: float | None = None) -> None:
        for thread in self._threads:
            thread.join(timeout)

    @property
    def error(self) -> str:
        return self._error

    # ---------- 合成 ----------

    def _produce(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._pending and not self._closed and not self._cancelled:
                        self._cond.wait(0.2)
                    if self._cancelled:
                        return
                    if not self._pending:
                        if self._closed:
                            return
                        continue
                    sentence = self._pending.pop(0)
                samples, rate = self.engine.synthesize(sentence)
                if samples is not None and len(samples) > 0:
                    self._audio.put((samples, rate))
        except Exception as exc:  # 合成挂了也要让播放线程收尾
            self._fail(f"合成失败：{type(exc).__name__}: {exc}")
        finally:
            self._audio.put(None)

    def _fail(self, message: str) -> None:
        self._error = message
        callback = self._on_error
        if callback is not None:
            try:
                callback(message)
            except Exception:
                pass

    # ---------- 播放 ----------

    def _play(self) -> None:
        stream = None
        rate = 0
        played = False
        try:
            import sounddevice as sd

            while True:
                if self._cancelled:  # 取消后队列里残留的音频也不能再播
                    break
                got = True
                try:
                    item = self._audio.get_nowait()
                except queue.Empty:
                    got = False
                    item = None

                if not got:
                    if self._cancelled:
                        break
                    if stream is None:
                        # 第一段音频还没合成好：先别开设备
                        try:
                            item = self._audio.get(timeout=0.2)
                            got = True
                        except queue.Empty:
                            continue
                    else:
                        # 已经在播：用静音填住，别让设备欠载出"咔"的一声
                        stream.write(_silence(rate, self.GAP_BLOCK_MS))
                        continue
                if item is None:
                    break
                samples, sample_rate = item
                if stream is None or sample_rate != rate:
                    if stream is not None:
                        self._close_stream(stream, rate)
                    rate = int(sample_rate)
                    stream = self._open_stream(sd, rate)
                    if not played and self._on_start is not None:
                        self._on_start()
                elif played:
                    # 两句紧贴着放会像抢话；中间留一口气，听感松一点
                    stream.write(_silence(rate, self.SENTENCE_GAP_MS))
                stream.write(samples)
                played = True
        except Exception as exc:
            if not self._cancelled:  # 主动取消时 abort 抛的异常不算失败
                self._fail(f"播放失败：{type(exc).__name__}: {exc}")
        finally:
            if stream is not None:
                self._close_stream(stream, rate)
            self._stream = None
            self.played = played
            callback = self._on_done
            if callback is not None:
                try:
                    callback(bool(played) and not self._cancelled)
                except Exception:
                    pass

    def _open_stream(self, sd, rate: int):
        # latency="high"：给驱动更大的缓冲，换设备启动期不丢音
        stream = sd.OutputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            latency="high",
            blocksize=0,
        )
        stream.start()
        self._stream = stream  # 先登记，取消时才找得到它
        stream.write(_silence(rate, self.LEAD_MS))
        return stream

    def _close_stream(self, stream, rate: int) -> None:
        """补静音 → 等设备把缓冲区播完 → 再关。直接关会掐掉最后几个字。"""
        try:
            stream.write(_silence(rate, self.TAIL_MS))
            latency = float(getattr(stream, "latency", 0.2) or 0.2)
            time.sleep(min(0.6, latency * 2 + 0.05))
        except Exception:
            pass
        finally:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


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
    warmth: float = 0.16
    clarity: float = 0.06
    deharsh: float = 0.22
    space: float = 0.06
    compression: float = 0.30


# ---------- 「AI 音色」后处理 ----------
#
# 目标只有一个：**别硬**。
#
# 上一版是"清楚优先"——给 3.2kHz 以上加了 30% 的存在感，再压得很紧。听感上
# 就是发燥、发刺、没有呼吸，像在对着扩音器说话。现在改成"柔和优先"：
#   1. 低频补一点厚度（200Hz 以下），声音有身体，不是纸片
#   2. 4.5kHz 以上**压一点**（deharsh）而不是加，齿音和气声就收住了
#   3. 2.5–4.5kHz 只加一点点存在感，保证吐字还清楚
#   4. 压缩放轻：起伏留着才自然，压扁了一定发硬
#   5. 空间感再收一点，混响多了会显得"空、假"
#   6. 收尾用软限幅（tanh）代替硬削波，峰值附近不会突然变方


@dataclass
class VoiceFx:
    enabled: bool = True
    warmth: float = 0.16  # 低频厚度（0–0.3）
    clarity: float = 0.06  # 中高频存在感（0–0.6）；大了就发燥
    deharsh: float = 0.22  # 4.5kHz 以上柔化（0–0.6）；压齿音
    space: float = 0.06  # 空间感混入比例（0–0.3）
    compression: float = 0.30  # 压缩强度（0–1）；大了就"压扁"、没呼吸


def _lowpass(x, rate: int, cutoff: float):
    """一阶低通（用 FIR 近似单极点），够用且不改相位太多。"""
    import numpy as np

    alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / rate)
    n = max(8, int(rate / max(cutoff, 40.0)))
    kernel = alpha * (1.0 - alpha) ** np.arange(n)
    kernel = kernel / (kernel.sum() + 1e-12)
    return np.convolve(x, kernel, mode="same")


def _band(x, rate: int, low_hz: float, high_hz: float):
    """取一段频带：low_hz 以上、high_hz 以下。"""
    return _lowpass(x, rate, high_hz) - _lowpass(x, rate, low_hz)


def _compress(x, rate: int, amount: float, threshold: float = 0.18):
    """软压缩：超过门限的部分按比例压，并补一点增益。

    门限抬高、比例放低之后，句子内部的强弱起伏基本还在——那是"自然"的来源。
    """
    import numpy as np

    amount = max(0.0, min(1.0, amount))
    if amount <= 0:
        return x

    envelope = _lowpass(np.abs(x), rate, 60.0)
    ratio = 1.0 + 2.0 * amount
    gain = np.ones_like(envelope)
    loud = envelope > threshold
    gain[loud] = (threshold / envelope[loud]) ** (1.0 - 1.0 / ratio)
    return x * gain * (1.0 + 0.3 * amount)


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


def _soft_clip(y, ceiling: float = 0.96):
    """软限幅：峰值附近圆滑过渡，不会像硬 clip 那样突然变方（那是"炸"的来源）。"""
    import numpy as np

    ceiling = max(0.2, float(ceiling))
    y = np.asarray(y, dtype="float32")
    return (np.tanh(y / ceiling) * ceiling).astype("float32")


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
    """把合成出来的干声过一遍「AI 音色」处理（柔和优先）。"""
    import numpy as np

    fx = fx or VoiceFx()
    if not fx.enabled or samples is None or len(samples) == 0:
        return samples

    y = np.asarray(samples, dtype="float32")

    # 1. 厚度：200Hz 以下补一点，声音有身体
    y = y + fx.warmth * _lowpass(y, rate, 200.0)

    # 2. 存在感：只给 2.5–4.5kHz 加一点点，保证字清楚
    y = y + fx.clarity * _band(y, rate, 2500.0, 4500.0)

    # 3. 去刺：4.5kHz 以上压下去，齿音和气声就收住了
    if fx.deharsh > 0:
        y = y - fx.deharsh * (y - _lowpass(y, rate, 4500.0))

    # 4. 轻压缩 + 5. 一点空间
    y = _compress(y, rate, fx.compression)
    y = _reverb(y, rate, fx.space)

    # 6. 软限幅收尾（比硬削波自然）
    return _soft_clip(y, 0.96)


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

    def speak(self, text: str, on_start=None, on_error=None) -> bool:
        """念一段话（阻塞到播完）。调用方负责放在后台线程。

        切句 → 逐句合成 → 一路流播出去，整段只开一次音频设备。
        头尾静音和收尾等待都在 PlaybackSession 里，见那边的说明。
        """
        session = self.new_session(on_start=on_start, on_error=on_error)
        session.push(text)
        session.close()
        session.start()
        session.join()
        return session.played

    def new_session(self, on_start=None, on_done=None, on_error=None) -> PlaybackSession:
        """开一次播报会话（已经在跑了）。一边生成一边 push，最后 close。

        线程在这里就起起来：调用方很容易忘了 start()，而忘了的表现是"静默不出声"，
        很难查。start() 是幂等的，重复调用没关系。
        """
        session = PlaybackSession(
            self, on_start=on_start, on_done=on_done, on_error=on_error
        )
        session.start()
        return session


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
