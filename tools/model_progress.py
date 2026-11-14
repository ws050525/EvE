"""看模型下载进度：列 models/ 下的 gguf，隔 3 秒采两次算速度。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from eve_core.config import load_config

    cfg = load_config()
    expected = {}
    for model_id, model in cfg.models.items():
        if getattr(model, "weights_mb", 0):
            expected[Path(model.path).name] = model.weights_mb
        if getattr(model, "mmproj_mb", 0):
            expected[Path(model.mmproj).name] = model.mmproj_mb
except Exception:
    expected = {}


def scan() -> dict[Path, int]:
    base = ROOT / "models"
    if not base.is_dir():
        return {}
    found: dict[Path, int] = {}
    for pattern in ("*.gguf", "*.mmproj"):
        found.update({path: path.stat().st_size for path in base.rglob(pattern)})
    return found


def main() -> int:
    first = scan()
    if not first:
        print("models/ 下没有 gguf")
        return 1

    time.sleep(3)
    second = scan()

    print(f"{'文件':<58}{'大小':>10}{'进度':>10}{'速度':>12}")
    for path, size in sorted(second.items(), key=lambda kv: -kv[1]):
        name = path.name
        shown = name if len(name) <= 56 else name[:53] + "…"
        mb = size / 1048576
        grew = size - first.get(path, size)
        speed = grew / 3 / 1048576
        target = expected.get(name, 0)
        if target:
            pct = min(100.0, mb / target * 100)
            left = max(0.0, target - mb)
            eta = f" 剩 {left / speed / 60:.0f} 分钟" if speed > 0.01 else "（没在动）"
            print(f"{shown:<58}{mb:9.0f}M{pct:9.1f}%{speed:9.1f} MB/s{eta}")
        else:
            print(f"{shown:<58}{mb:9.0f}M{'':>10}{speed:9.1f} MB/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
