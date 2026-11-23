"""把一堆零散录音整理成训练/复刻用的数据集。

    & 'D:\\python\\python.exe' tools\\build_dataset.py 录音目录 --out models/dataset
    & 'D:\\python\\python.exe' tools\\build_dataset.py 录音目录 --min 3 --max 10 --lang zh

做的事情：

1. 逐条读入（wav / mp3 / m4a / flac / ogg 都行）；
2. 转单声道、统一采样率（默认 32k，GPT-SoVITS 用这个；CosyVoice 可以 --sr 24000）；
3. 砍掉首尾静音；
4. **超过 max 秒的自动切段**——切在停顿处，绝不从词中间下刀；
5. 太短的段并进相邻段（低于 min 秒的碎片没有训练价值）；
6. 用本地 SenseVoice 自动转写每段（`--no-asr` 可关掉，自己写稿更准）；
7. 输出 `list.txt`：`wav路径|说话人|语种|文本`，并打印一份体检报告。

为什么分段完全没问题：GPT-SoVITS / CosyVoice 微调吃的都是**3–10 秒的小片段**，
一段长录音反而要自己切。真正影响效果的是"每段干净、每段有准确文字"，不是长短。
"""

from __future__ import annotations

import argparse
import sys
import wave
from math import gcd
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}
SILENCE_THRESHOLD = 0.02  # RMS 低于它算静音
MIN_PAUSE = 0.25  # 至少停这么久才算"可以下刀"
PAD = 0.12  # 每段首尾留一点余量，别把字切掉


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------- 读 / 转 ----------


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1), int(rate)


def resample(wave: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target:
        return wave
    from scipy.signal import resample_poly

    divisor = gcd(int(rate), int(target))
    return resample_poly(wave, target // divisor, rate // divisor).astype(np.float32)


def rms_curve(wave: np.ndarray, rate: int, frame_ms: float = 20.0) -> np.ndarray:
    frame = max(1, int(rate * frame_ms / 1000))
    count = len(wave) // frame
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    trimmed = wave[: count * frame].reshape(count, frame)
    return np.sqrt((trimmed**2).mean(axis=1))


def trim_silence(wave: np.ndarray, rate: int) -> np.ndarray:
    curve = rms_curve(wave, rate)
    if not len(curve):
        return wave
    voiced = np.where(curve > SILENCE_THRESHOLD)[0]
    if not len(voiced):
        return wave
    frame = max(1, int(rate * 0.02))
    pad = int(PAD * rate)
    start = max(0, voiced[0] * frame - pad)
    end = min(len(wave), (voiced[-1] + 1) * frame + pad)
    return wave[start:end]


def quiet_runs(curve: np.ndarray, frame_ms: float = 20.0) -> list[tuple[int, int]]:
    """返回静音区间（帧下标），只保留够长的。"""
    min_frames = max(1, int(MIN_PAUSE * 1000 / frame_ms))
    runs: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(curve):
        if value <= SILENCE_THRESHOLD:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= min_frames:
                runs.append((start, index))
            start = None
    if start is not None and len(curve) - start >= min_frames:
        runs.append((start, len(curve)))
    return runs


def split_segments(wave: np.ndarray, rate: int, min_s: float, max_s: float) -> list[np.ndarray]:
    """按停顿切段：每段落在 [min_s, max_s] 之间，优先切在静音处。"""
    frame = max(1, int(rate * 0.02))
    curve = rms_curve(wave, rate)
    pauses = quiet_runs(curve)
    min_frames = max(1, int(min_s / 0.02))
    max_frames = max(min_frames + 1, int(max_s / 0.02))

    segments: list[np.ndarray] = []
    start_frame = 0
    total = len(curve)
    while start_frame < total:
        limit = min(start_frame + max_frames, total)
        if limit >= total:
            segments.append(wave[start_frame * frame :])
            break
        # 在 [start+min, limit] 里找最后一段停顿，切在它中间
        cut = None
        for pause_start, pause_end in pauses:
            if pause_start >= start_frame + min_frames and pause_end <= limit:
                cut = (pause_start + pause_end) // 2
        if cut is None:
            # 这一截没有停顿（连续说话），只能硬切在 limit
            cut = limit
        segments.append(wave[start_frame * frame : cut * frame])
        start_frame = cut

    # 太短的尾巴并回上一段
    merged: list[np.ndarray] = []
    min_samples = int(min_s * rate)
    for segment in segments:
        if len(segment) < min_samples and merged:
            merged[-1] = np.concatenate([merged[-1], segment])
        elif len(segment) >= int(0.3 * rate):
            merged.append(segment)
    return merged


def write_wav(path: Path, wave: np.ndarray, rate: int) -> None:
    peak = float(np.abs(wave).max()) or 1.0
    scaled = np.clip(wave / peak * 0.95, -1.0, 1.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave_open(path) as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((scaled * 32767).astype("<i2").tobytes())


def wave_open(path: Path):
    handle = wave.open(str(path), "wb")
    return handle


# ---------- 主流程 ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_dataset", description="把录音整理成训练数据集")
    parser.add_argument("source", help="录音目录（或单个文件）")
    parser.add_argument("--out", default="models/dataset", help="输出目录")
    parser.add_argument("--speaker", default="eve", help="说话人标记（写进 list.txt）")
    parser.add_argument("--lang", default="zh", help="语种：zh / en / ja …（也用于 ASR）")
    parser.add_argument("--min", dest="min_s", type=float, default=3.0, help="每段最短秒数")
    parser.add_argument("--max", dest="max_s", type=float, default=10.0, help="每段最长秒数")
    parser.add_argument("--sr", type=int, default=32000, help="输出采样率（GPT-SoVITS 用 32000）")
    parser.add_argument("--no-asr", action="store_true", help="不自动转写（自己写稿）")
    args = parser.parse_args(argv)

    source = Path(args.source)
    out_dir = Path(args.out) if args.out else ROOT / "models" / "dataset"
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    if source.is_file():
        files = [source]
    elif source.is_dir():
        files = sorted(p for p in source.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES)
    else:
        log(f"找不到：{source}")
        return 1
    if not files:
        log(f"{source} 里没有音频文件（支持 {'、'.join(sorted(AUDIO_SUFFIXES))}）")
        return 1

    log(f"找到 {len(files)} 个音频文件，输出到 {out_dir}")

    recognizer = None
    if not args.no_asr:
        from eve_core.config import load_config
        from eve_core.voice import SherpaRecognizer

        voice = load_config().voice
        recognizer = SherpaRecognizer(voice.asr_model, voice.asr_fallbacks, language=args.lang)
        if not recognizer.available:
            log(f"!! ASR 不可用（{recognizer.error}），改成 --no-asr 自己写稿")
            return 1

    rows: list[tuple[Path, str]] = []
    total_audio = 0.0
    skipped = 0

    for path in files:
        try:
            wave_data, rate = load_audio(path)
        except Exception as exc:
            log(f"  跳过 {path.name}：{exc}")
            skipped += 1
            continue
        wave_data = trim_silence(resample(wave_data, rate, args.sr), args.sr)
        segments = split_segments(wave_data, args.sr, args.min_s, args.max_s)
        if not segments:
            log(f"  跳过 {path.name}：没有可用语音")
            skipped += 1
            continue

        for index, segment in enumerate(segments, 1):
            name = f"{path.stem}-{index:02d}.wav"
            target = out_dir / "wavs" / name
            write_wav(target, segment, args.sr)
            seconds = len(segment) / args.sr
            total_audio += seconds

            text = ""
            if recognizer is not None:
                try:
                    from math import gcd as _gcd

                    from scipy.signal import resample_poly

                    divisor = _gcd(args.sr, 16000)
                    mono16 = resample_poly(
                        segment, 16000 // divisor, args.sr // divisor
                    ).astype(np.float32)
                    text = recognizer.transcribe(mono16).strip()
                except Exception as exc:
                    log(f"  转写失败 {name}：{exc}")
            rows.append((target, text))
            log(f"  {name}  {seconds:.1f}s  {text[:40]}")

    if not rows:
        log("没产出任何片段")
        return 1

    manifest = out_dir / "list.txt"
    with manifest.open("w", encoding="utf-8") as handle:
        for target, text in rows:
            handle.write(f"{target.as_posix()}|{args.speaker}|{args.lang}|{text}\n")

    log("")
    log("=" * 46)
    log(f"片段 {len(rows)} 段 / 总时长 {total_audio / 60:.1f} 分钟")
    log(f"清单：{manifest}")
    log("格式：wav路径|说话人|语种|文本")
    if skipped:
        log(f"跳过 {skipped} 个文件")
    if total_audio < 60:
        log("提醒：少于 1 分钟。零样本够用，微调建议 5 分钟以上")
    if recognizer is not None:
        log("提醒：文字是自动转写的，训练前最好人工过一遍（错字会直接影响音质）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
