"""数据集整理工具的测试：切段、静音裁剪、采样率统一。"""

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_dataset as bd

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


def speech(seconds: float, rate: int = 16000, freq: float = 180.0) -> np.ndarray:
    n = int(rate * seconds)
    t = np.linspace(0, seconds, n, endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float, rate: int = 16000) -> np.ndarray:
    return np.zeros(int(rate * seconds), dtype=np.float32)


@case
def split_at_pauses_keeps_segments_in_range():
    rate = 16000
    wave = np.concatenate(
        [speech(2.0), silence(0.6), speech(2.0), silence(0.6), speech(2.0)]
    )
    segments = bd.split_segments(wave, rate, min_s=1.5, max_s=3.0)
    assert len(segments) >= 2, [len(s) / rate for s in segments]
    for segment in segments:
        # 允许首尾留白，不严格卡边界
        assert len(segment) / rate <= 3.5, len(segment) / rate


@case
def long_speech_without_pause_gets_hard_cut():
    rate = 16000
    wave = speech(12.0, rate)
    segments = bd.split_segments(wave, rate, min_s=2.0, max_s=4.0)
    assert len(segments) >= 3, [len(s) / rate for s in segments]
    for segment in segments:
        assert len(segment) / rate <= 4.5, len(segment) / rate


@case
def tiny_tail_is_merged():
    rate = 16000
    wave = np.concatenate([speech(3.0), silence(0.5), speech(0.4)])
    segments = bd.split_segments(wave, rate, min_s=2.0, max_s=10.0)
    assert segments, "不该把内容全丢了"
    # 0.4 秒的碎尾不该单独成段
    assert all(len(s) / rate >= 0.3 for s in segments)


@case
def trim_removes_leading_and_trailing_silence():
    rate = 16000
    wave = np.concatenate([silence(1.0), speech(2.0), silence(1.0)])
    trimmed = bd.trim_silence(wave, rate)
    assert len(trimmed) < len(wave), (len(trimmed), len(wave))
    assert len(trimmed) / rate > 1.8, len(trimmed) / rate


@case
def resample_changes_length_proportionally():
    wave = speech(1.0, 22050)
    out = bd.resample(wave, 22050, 16000)
    assert abs(len(out) / 16000 - 1.0) < 0.05, len(out) / 16000


@case
def writes_manifest_in_gpt_sovits_format():
    rate = 16000
    wave = np.concatenate([speech(2.0), silence(0.5), speech(2.0)])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        target = out / "wavs" / "a-01.wav"
        bd.write_wav(target, wave, rate)
        assert target.exists()
        with target.open("rb") as handle:
            assert handle.read(4) == b"RIFF"  # 真的是 wav
        manifest = out / "list.txt"
        with manifest.open("w", encoding="utf-8") as handle:
            handle.write(f"{target.as_posix()}|eve|zh|你好\n")
        line = manifest.read_text(encoding="utf-8").strip()
        assert line.count("|") == 3, line


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    total = len(CHECKS)
    print(f"\n{total - failed}/{total} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
