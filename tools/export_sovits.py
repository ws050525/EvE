"""把训练 checkpoint 导出成推理权重（s2_train 的 savee 那一步）。

    & 'D:\\python\\python.exe' tools\\export_sovits.py cn-v2

s2_train 只在真正训练过 epoch 时才导出；如果训练已经结束（epoch 到位），
再跑一次不会导出任何东西。所以单独做这一步。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / ".vendor" / "GPT-SoVITS"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "GPT_SoVITS"))
sys.path.insert(0, str(REPO))

import torch
from process_ckpt import savee

exp = sys.argv[1] if len(sys.argv) > 1 else "cn-v2"
exp_dir = REPO / "logs" / exp
ckpt_path = exp_dir / "logs_s2_v2" / "G_233333333333.pth"
if not ckpt_path.exists():
    print(f"找不到 {ckpt_path}")
    raise SystemExit(1)

cfg_path = REPO / "logs" / "tmp" / f"{exp}_s2.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))


from eve_core.tts_gptsovits import wrap_attrdict

hps = wrap_attrdict(cfg)
hps["name"] = exp
hps["save_weight_dir"] = str(REPO / "SoVITS_weights_v2")
hps.setdefault("data", {})["exp_dir"] = str(exp_dir)

state = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
ckpt = state.get("model", state)
epoch = int(state.get("iteration", 0)) or 10
name = f"{exp}_e{epoch}"
print(savee(ckpt, name, epoch, 0, hps, model_version="v2"))
out = Path(hps.save_weight_dir) / f"{name}.pth"
print("产出:", out, f"{out.stat().st_size/1048576:.0f}MB" if out.exists() else "（没生成）")
