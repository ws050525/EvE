"""把同一句话用几组播报参数渲染成 wav，方便 A/B 试听。

    & 'D:\\python\\python.exe' tools\\tts_preview.py            # 渲染到 .tmp\\preview
    & 'D:\\python\\python.exe' tools\\tts_preview.py --play      # 渲染完直接播一遍
    & 'D:\\python\\python.exe' tools\\tts_preview.py --out D:\\x  # 换个输出目录

为什么要这个工具：声音"硬不硬"是主观判断，光看参数没用。渲染出几组文件，
听一遍再决定改哪几个数，比反复改代码快得多。

顺带打印客观指标：
- 谱心（spectral centroid）：整体"亮度"，越低越柔。中文女声 2300–2600Hz 比较耐听，
  上 3000Hz 就偏刺。
- 4.5kHz 以上能量占比：齿音、气声的量，高了发燥。
- 峰均比（crest）：动态，压得越狠越小，听着越"压扁"。
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eve_core.config import load_config
from eve_core.tts import PiperTts, VoiceFx, list_voice_models

TEXT = (
    "我刚看了一眼，桌面上那三个文件已经按日期整理好了。"
    "你要是想再确认一下，我可以把改动一条条念给你听。"
)


def save(samples, rate: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            (np.clip(samples, -1.0, 1.0) * 32767).astype("int16").tobytes()
        )


def metrics(samples, rate: int) -> dict:
    x = np.asarray(samples, dtype="float64")
    if x.size == 0:
        return {}
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    total = float(spectrum.sum()) + 1e-12
    peak = float(np.abs(x).max())
    rms = float(np.sqrt((x**2).mean()))
    return {
        "centroid": float((spectrum * freqs).sum() / total),
        "high": float(spectrum[freqs > 4500].sum() / total) * 100,
        "peak_db": 20 * np.log10(peak + 1e-9),
        "rms_db": 20 * np.log10(rms + 1e-9),
        "crest": 20 * np.log10(peak / (rms + 1e-9)),
        "seconds": len(x) / rate,
    }


def render(label: str, name: str, out: Path, tts_factory, play: bool, text: str) -> dict:
    tts = tts_factory()
    if not tts.available:
        print(f"{label:<20} 不可用：{tts.error}")
        return {}
    samples, rate = tts.synthesize(text)
    if samples is None:
        print(f"{label:<20} 合成失败")
        return {}
    path = out / f"{name}.wav"
    save(samples, rate, path)
    m = metrics(samples, rate)
    print(
        f"{label:<20} 谱心 {m['centroid']:6.0f}Hz  4.5k+ {m['high']:5.1f}%  "
        f"RMS {m['rms_db']:6.1f}dB  峰均 {m['crest']:4.1f}dB  {m['seconds']:5.2f}s  → {path.name}"
    )
    if play:
        import sounddevice as sd

        sd.play(samples, rate)
        sd.wait()
    return m


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    voice = cfg.voice

    parser = argparse.ArgumentParser(prog="tts_preview", description="播报参数试听")
    parser.add_argument("--out", default=str(ROOT / ".tmp" / "preview"), help="输出目录")
    parser.add_argument("--play", action="store_true", help="渲染完逐个播放")
    parser.add_argument("--text", default=TEXT, help="要念的文本")
    args = parser.parse_args(argv)

    text = args.text
    out = Path(args.out)

    current = VoiceFx(
        enabled=voice.tts_fx,
        warmth=voice.tts_fx_warmth,
        clarity=voice.tts_fx_clarity,
        deharsh=voice.tts_fx_deharsh,
        space=voice.tts_fx_space,
        compression=voice.tts_fx_compression,
    )

    def make(model=None, speed=None, fx=None):
        return PiperTts(
            model or voice.tts_model,
            speed=voice.tts_speed if speed is None else speed,
            speaker=voice.tts_speaker,
            volume=voice.tts_volume,
            noise_scale=voice.tts_noise_scale,
            noise_scale_w=voice.tts_noise_scale_w,
            fx=fx or current,
        )

    print(f"文本：{text}\n")
    render("当前配置", "01-current", out, make, args.play, text)
    render(
        "更柔（速度 1.0）",
        "02-softer",
        out,
        lambda: make(
            speed=1.0,
            fx=VoiceFx(
                warmth=0.20, clarity=0.02, deharsh=0.32, space=0.04, compression=0.15
            ),
        ),
        args.play,
        text,
    )
    render(
        "干声（关后处理）",
        "03-dry",
        out,
        lambda: make(fx=VoiceFx(enabled=False)),
        args.play,
        text,
    )

    for index, item in enumerate(list_voice_models(Path(voice.tts_model).parent), start=4):
        render(
            f"音色 {item['label'][:12]}",
            f"{index:02d}-{item['id']}",
            out,
            lambda path=item["path"]: make(model=path),
            args.play,
            text,
        )

    print(f"\n输出目录：{out}")
    print("想更柔：把 tts_fx_deharsh 往上、tts_fx_clarity 和 tts_fx_compression 往下；")
    print("想更亮：反过来。tts_speed 1.0–1.1 之间找自己喜欢的节奏。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
