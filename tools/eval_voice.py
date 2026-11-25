"""用微调后的权重生成试听样本。

    & 'D:\\python\\python.exe' tools\\eval_voice.py --exp cn-v2

会找最新的 SoVITS（G_*.pth）和 GPT（*.ckpt）权重，合成三段 30 秒左右的文本：
全中文 / 全英文 / 中英混合，输出到 `.tmp/preview/`。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPO = ROOT / ".vendor" / "GPT-SoVITS"
TEXTS = {
    "zh": ".tmp/evaltext/zh.txt",
    "en": ".tmp/evaltext/en.txt",
    "mix": ".tmp/evaltext/mix.txt",
}


def newest(folder: Path, pattern: str) -> Path | None:
    if not folder.is_dir():
        return None
    items = [p for p in folder.glob(pattern) if p.is_file()]
    return max(items, key=lambda p: p.stat().st_mtime) if items else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval_voice", description="用微调权重生成试听")
    parser.add_argument("--exp", default="cn-v2")
    parser.add_argument("--ref-wav", default="models/voices/ref-cn-game.wav")
    parser.add_argument(
        "--ref-text",
        default="自从认识了老师，爱丽丝的内部就自动编写了一段程序。",
    )
    parser.add_argument("--out", default=".tmp/preview")
    args = parser.parse_args(argv)

    exp_dir = REPO / "logs" / args.exp
    # 优先用导出的推理权重（含 config），训练断点（logs_s2_v2/G_*.pth）不能被推理直接加载
    vits = newest(REPO / "SoVITS_weights_v2", "*.pth") or newest(exp_dir / "logs_s2_v2", "G_*.pth")
    gpt = newest(REPO / "GPT_weights_v2", "*.ckpt") or newest(exp_dir / "logs_s1_v2", "*.ckpt")
    if vits is None:
        print(f"没找到 SoVITS 权重：{exp_dir / 'logs_s2_v2'}")
        return 1
    if gpt is None:
        print("没找到 GPT 权重（GPT_weights_v2 / logs_s1_v2）")
        return 1
    print(f"SoVITS: {vits.name}（{vits.stat().st_size / 1048576:.0f}MB）")
    print(f"GPT   : {gpt.name}（{gpt.stat().st_size / 1048576:.0f}MB）")

    from eve_core.tts_gptsovits import GptSovitsTts

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = GptSovitsTts(
        ref_wav=ROOT / args.ref_wav,
        ref_text=args.ref_text,
        speed=1.0,
        half=True,
        t2s_path=gpt,
        vits_path=vits,
    )
    print("可用:", engine.available, engine.error or "")
    if not engine.available:
        return 1

    import numpy as np
    import soundfile as sf

    engine._ensure()
    for key, text_file in TEXTS.items():
        path = ROOT / text_file
        if not path.exists():
            print(f"跳过 {key}：缺 {path}")
            continue
        text = " ".join(path.read_text(encoding="utf-8").split())
        lang = "en" if key == "en" else "zh"
        engine.text_lang = lang
        t0 = time.perf_counter()
        wave, rate = engine.synthesize(text)
        dt = time.perf_counter() - t0
        if wave is None:
            print(f"[{key}] 失败：{engine.error[:150]}")
            continue
        target = out_dir / f"trained-{key}.wav"
        sf.write(str(target), np.clip(wave, -1, 1), rate)
        print(f"[{key}] {len(text)} 字 → 音频 {len(wave) / rate:.1f}s / 合成 {dt:.1f}s → {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
