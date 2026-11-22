"""把一段录音整理成 CosyVoice 复刻用的参考音频。

    & 'D:\\python\\python.exe' tools\\prepare_ref.py 原音频.wav
    & 'D:\\python\\python.exe' tools\\prepare_ref.py 原音频.mp3 --out models/voices/my-voice.wav

做三件事：转单声道、砍掉首尾静音、峰值归一。
复刻质量几乎全看这段样本——背景有音乐、有回声、或者只截了三秒，学出来就会"糊"。
所以宁可选一段 8–10 秒、干净、语速正常的独白。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGET_RATE = 22050
MIN_SECONDS = 3.0
MAX_SECONDS = 12.0


def load(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1).astype(np.float32), int(rate)


def resample(wave: np.ndarray, rate: int, target: int = TARGET_RATE) -> np.ndarray:
    """用 scipy 多相重采样。不用 librosa——它会拉进 numba，
    在这台机器上会撞 LLVM 符号缺失（__svml_cosf8_ha）直接崩。"""
    if rate == target:
        return wave
    from math import gcd

    from scipy.signal import resample_poly

    divisor = gcd(int(rate), int(target))
    up, down = target // divisor, rate // divisor
    return resample_poly(wave, up, down).astype(np.float32)


def trim(wave: np.ndarray, rate: int, threshold: float = 0.02) -> np.ndarray:
    """按 50ms 帧找有声区间，砍掉首尾静音（留 0.1 秒余量）。"""
    frame = max(1, int(rate * 0.05))
    count = len(wave) // frame
    if count == 0:
        return wave
    rms = np.array(
        [np.sqrt(np.mean(wave[i * frame : (i + 1) * frame] ** 2)) for i in range(count)]
    )
    voiced = np.where(rms > threshold)[0]
    if len(voiced) == 0:
        return wave
    start = max(0, (voiced[0] - 2) * frame)
    end = min(len(wave), (voiced[-1] + 3) * frame)
    return wave[start:end]


def cut_at_pause(wave: np.ndarray, rate: int, limit_seconds: float) -> np.ndarray:
    """把音频截到 limit_seconds 以内，但**切在停顿处**。

    直接从中间剪断会让参考音频停在半个字上，CosyVoice 会学出一个"起始咔哒声"
    （实测过：每次合成开头都有一小段杂音）。所以先找最后一个停顿再切。
    """
    limit = int(limit_seconds * rate)
    if len(wave) <= limit:
        return wave
    frame = max(1, int(rate * 0.02))
    count = min(len(wave), limit) // frame
    if count < 10:
        return wave[:limit]
    rms = np.array(
        [np.sqrt(np.mean(wave[i * frame : (i + 1) * frame] ** 2)) for i in range(count)]
    )
    quiet = rms < 0.02
    # 从后往前找一段 >=120ms 的静音，切在它的中间
    run_start = None
    for i in range(count - 1, -1, -1):
        if quiet[i]:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and (run_start - i) * frame >= int(0.12 * rate):
                cut = (i + (run_start - i) // 2) * frame
                return wave[:cut]
            run_start = None
    return wave[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepare_ref", description="整理复刻用的参考音频")
    parser.add_argument("source", help="原始音频（wav / mp3 / m4a）")
    parser.add_argument("--out", default="", help="输出路径，默认 models/voices/ref-<原名>.wav")
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.exists():
        print(f"找不到文件：{source}")
        return 1

    wave, rate = load(source)
    seconds = len(wave) / rate
    print(f"读入 {source.name}：{seconds:.1f}s @ {rate}Hz")
    if seconds < MIN_SECONDS:
        print(f"⚠ 太短了（{seconds:.1f}s）。复刻建议 5–10 秒，太短学不出音色")

    wave = trim(resample(wave, rate), TARGET_RATE)
    wave = cut_at_pause(wave, TARGET_RATE, args.max_seconds)
    peak = float(np.abs(wave).max()) or 1.0
    wave = (wave / peak) * 0.95

    out = Path(args.out) if args.out else ROOT / "models" / "voices" / f"ref-{source.stem}.wav"
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    import soundfile as sf

    sf.write(str(out), wave, TARGET_RATE)

    print(f"写出 {out}（{len(wave) / TARGET_RATE:.1f}s @ {TARGET_RATE}Hz 单声道）")
    print("\n接下来在 config.toml 里填：")
    print(f'  cosy_mode = "zero_shot"')
    try:
        shown = out.relative_to(ROOT).as_posix()
    except ValueError:
        shown = str(out)
    print(f'  cosy_ref_wav = "{shown}"')
    print('  cosy_ref_text = "这段音频里说的那句话（要一字不差）"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
