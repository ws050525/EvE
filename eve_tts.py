"""语音播调试听工具：改参数 → 立刻听，不用开界面。

    & 'D:\\python\\python.exe' eve_tts.py "你好，我是伊芙"
    & 'D:\\python\\python.exe' eve_tts.py "你好" --speed 1.15 --volume 0.8
    & 'D:\\python\\python.exe' eve_tts.py "你好" --model models/voice/vits-zh-hf-theresa
    & 'D:\\python\\python.exe' eve_tts.py --list
    & 'D:\\python\\python.exe' eve_tts.py "你好" --save sample.wav --no-play

不带参数时用 config.toml 里的设置念一句默认文本。
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eve_core.config import load_config
from eve_core.tts import PiperTts


def list_models(root: Path) -> int:
    voice_dir = root / "models" / "voice"
    if not voice_dir.is_dir():
        print(f"没有模型目录：{voice_dir}")
        return 1
    found = 0
    for path in sorted(voice_dir.iterdir()):
        if not path.is_dir():
            continue
        # 只认 VITS：ASR / KWS 模型也带 onnx 和 tokens.txt，
        # 但拿它们去 VITS 加载会直接把进程打掉。
        if not path.name.startswith("vits"):
            continue
        if not any(p.suffix == ".onnx" for p in path.iterdir()):
            continue
        found += 1
        try:
            tts = PiperTts(path)
            if not tts.available:
                print(f"  {path.name}  （{tts.error}）")
                continue
            tts._ensure()
            print(
                f"  {path.name}  采样率 {tts._ensure().sample_rate}"
                f"  说话人 {tts._ensure().num_speakers}"
            )
        except Exception as exc:
            print(f"  {path.name}  （加载失败：{exc}）")
    if not found:
        print("没找到可用的 TTS 模型")
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    voice = cfg.voice

    parser = argparse.ArgumentParser(prog="eve_tts", description="语音播调试听")
    parser.add_argument("text", nargs="?", default="你好呀，我是伊芙。今天想让我帮你做点什么？")
    parser.add_argument("--model", default=voice.tts_model, help="模型目录")
    parser.add_argument("--speed", type=float, default=voice.tts_speed)
    parser.add_argument("--speaker", type=int, default=voice.tts_speaker)
    parser.add_argument("--volume", type=float, default=voice.tts_volume)
    parser.add_argument("--noise", type=float, default=voice.tts_noise_scale)
    parser.add_argument("--noise-w", type=float, default=voice.tts_noise_scale_w)
    parser.add_argument("--save", help="保存成 wav")
    parser.add_argument("--no-play", action="store_true", help="只合成不播放")
    parser.add_argument("--list", action="store_true", help="列出可用模型")
    args = parser.parse_args(argv)

    if args.list:
        return list_models(cfg.root)

    tts = PiperTts(
        args.model,
        speed=args.speed,
        speaker=args.speaker,
        volume=args.volume,
        noise_scale=args.noise,
        noise_scale_w=args.noise_w,
    )
    if not tts.available:
        print(f"不可用：{tts.error}")
        return 2

    print(f"模型 {Path(args.model).name}  语速 {tts.speed}  音量 {tts.volume}  "
          f"起伏 {tts.noise_scale}  停顿 {tts.noise_scale_w}  说话人 {tts.speaker}")
    print(f"文本：{args.text}")

    samples, rate = tts.synthesize(args.text)
    if samples is None:
        print("合成失败：模型可能不认识这些字，或者模型目录不对")
        return 1
    print(f"合成 {len(samples)/rate:.2f}s @ {rate}Hz")

    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(rate)
            fh.writeframes((samples * 32767).astype("int16").tobytes())
        print(f"已保存：{path}")

    if not args.no_play:
        import sounddevice as sd

        print("播放…")
        sd.play(samples, rate)
        sd.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
