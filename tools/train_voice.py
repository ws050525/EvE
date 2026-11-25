"""在本地数据上微调 GPT-SoVITS（v2）。

    & 'D:\\python\\python.exe' tools\\train_voice.py models/dataset-cn --exp cn-v2

流程（和 webui.py 里点"训练"的按钮等价，只是没有界面）：

1. `1-get-text.py`     文本 → BERT 特征 + 音素
2. `2-get-hubert-wav32k.py` 音频 → HuBERT 特征
3. `3-get-semantic.py` 语义 token
4. `s2_train.py`       SoVITS（声学模型）
5. `s1_train.py`       GPT（音色/语调）

产物在 `.vendor/GPT-SoVITS/logs/<exp>/`：
- `logs_s2_v2/G_*.pth`    声学权重
- `logs_s1_v2/*.ckpt`     GPT 权重

显卡 8G 的默认参数：SoVITS batch 6 / 12 epoch，GPT batch 6 / 16 epoch。
数据少（10 分钟级）时这两个数足够，再多容易过拟合。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / ".vendor" / "GPT-SoVITS"
PRE = REPO / "GPT_SoVITS" / "pretrained_models"
PY = sys.executable

S2G = PRE / "gsv-v2final-pretrained" / "s2G2333k.pth"
S2D = PRE / "gsv-v2final-pretrained" / "s2D2333k.pth"
S1 = PRE / "gsv-v2final-pretrained" / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
BERT = PRE / "chinese-roberta-wwm-ext-large"
HUBERT = PRE / "chinese-hubert-base"


def log(msg: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} {msg}", flush=True)


def run(cmd: list[str], env: dict[str, str], log_path: Path) -> None:
    log(f"$ {' '.join(cmd[:3])} …")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n===== {time.strftime('%H:%M:%S')} {' '.join(cmd)}\n")
        handle.flush()
        code = subprocess.call(cmd, cwd=str(REPO), env=env, stdout=handle, stderr=subprocess.STDOUT)
    if code != 0:
        raise SystemExit(f"命令失败（{code}），看日志：{log_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="train_voice", description="微调 GPT-SoVITS")
    parser.add_argument("dataset", help="dataset 目录（含 list.txt 和 wavs/）")
    parser.add_argument("--exp", default="cn-v2", help="实验名")
    parser.add_argument("--s2-epochs", type=int, default=12)
    parser.add_argument("--s1-epochs", type=int, default=16)
    parser.add_argument("--batch", type=int, default=6)
    parser.add_argument("--skip-preprocess", action="store_true", help="跳过预处理直接训练")
    parser.add_argument("--skip-s2", action="store_true", help="跳过 SoVITS，只练 GPT")
    parser.add_argument("--redo-preprocess", action="store_true", help="强制重跑预处理")
    args = parser.parse_args(argv)

    dataset = Path(args.dataset)
    if not dataset.is_absolute():
        dataset = ROOT / dataset
    list_src = dataset / "list.txt"
    wavs = dataset / "wavs"
    if not list_src.exists() or not wavs.is_dir():
        log(f"数据集不完整：{list_src} / {wavs}")
        return 1

    exp_root = REPO / "logs"
    exp_dir = exp_root / args.exp
    exp_dir.mkdir(parents=True, exist_ok=True)
    # 训练脚本会往这两个子目录里存权重，但自己不会建（webui 里是按钮前建的）
    (REPO / "GPT_weights_v2").mkdir(exist_ok=True)
    (REPO / "SoVITS_weights_v2").mkdir(exist_ok=True)
    (exp_dir / "logs_s2_v2").mkdir(exist_ok=True)
    (exp_dir / "logs_s1_v2").mkdir(exist_ok=True)
    log_path = exp_dir / "train.log"
    log(f"实验目录 {exp_dir}")

    # 预处理脚本要的是"文件名|说话人|语种|文本"，路径另外给
    rows = []
    for line in list_src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) != 4:
            continue
        wav_path = Path(parts[0])
        rows.append(f"{wav_path.name}|{parts[1]}|{parts[2]}|{parts[3]}")
    if not rows:
        log("list.txt 是空的")
        return 1
    list_dst = exp_dir / "list.txt"
    list_dst.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log(f"{len(rows)} 条样本")

    base_env = os.environ.copy()
    base_env.update(
        {
            "inp_text": str(list_dst),
            "inp_wav_dir": str(wavs),
            "exp_name": args.exp,
            "opt_dir": str(exp_dir),
            "bert_pretrained_dir": str(BERT),
            "cnhubert_base_dir": str(HUBERT),
            # 3-get-semantic 要用声学权重和它的配置定位版本
            "pretrained_s2G": str(S2G),
            "s2config_path": str(REPO / "GPT_SoVITS" / "configs" / "s2.json"),
            "i_part": "0",
            "all_parts": "1",
            "_CUDA_VISIBLE_DEVICES": "0",
            "is_half": "True",
            "version": "v2",
            "hz": "25hz",
            # 预处理脚本按 "text.xxx" / "tools.xxx" 导入，
            # 这两个包在 GPT_SoVITS/ 和仓库根下，用脚本方式跑不会自动加进去
            "PYTHONPATH": os.pathsep.join(
                [str(REPO / "GPT_SoVITS"), str(REPO), os.environ.get("PYTHONPATH", "")]
            ),
        }
    )

    steps = [
        ("1 文本特征", [PY, "-s", "GPT_SoVITS/prepare_datasets/1-get-text.py"]),
        ("2 HuBERT", [PY, "-s", "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py"]),
        ("3 语义 token", [PY, "-s", "GPT_SoVITS/prepare_datasets/3-get-semantic.py"]),
    ]
    if args.redo_preprocess:
        args.skip_preprocess = False
    done_marker = exp_dir / "2-name2text-0.txt"
    have_semantic = exp_dir / "6-name2semantic-0.tsv"
    if args.skip_preprocess or (done_marker.exists() and have_semantic.exists()):
        log("预处理已完成，跳过（要重跑加 --redo-preprocess）")
    else:
        for label, cmd in steps:
            log(f"预处理 {label}")
            run(cmd, base_env, log_path)

    # 预处理产出带分片号（-0），训练脚本认的是不带号的名字
    for single in ("2-name2text-0.txt", "6-name2semantic-0.tsv"):
        src = exp_dir / single
        dst = exp_dir / single.replace("-0.", ".")
        if src.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            log(f"合并分片 {src.name} → {dst.name}")

    # ---------- SoVITS ----------
    tmp = exp_root / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    s2_cfg = json.loads((REPO / "GPT_SoVITS" / "configs" / "s2.json").read_text(encoding="utf-8"))
    s2_cfg["train"].update(
        {
            "batch_size": args.batch,
            "epochs": args.s2_epochs,
            "text_low_lr_rate": 0.4,
            "pretrained_s2G": str(S2G),
            "pretrained_s2D": str(S2D),
            "if_save_latest": True,
            "if_save_every_weights": True,
            "save_every_epoch": 4,
            "gpu_numbers": "0",
            "grad_ckpt": False,
            "lora_rank": 32,
        }
    )
    s2_cfg["model"]["version"] = "v2"
    s2_cfg.setdefault("data", {})["num_workers"] = 0
    s2_cfg["data"]["exp_dir"] = s2_cfg["s2_ckpt_dir"] = str(exp_dir)
    s2_cfg["save_weight_dir"] = "SoVITS_weights_v2"
    s2_cfg["name"] = args.exp
    s2_cfg["version"] = "v2"
    s2_path = tmp / f"{args.exp}_s2.json"
    s2_path.write_text(json.dumps(s2_cfg, ensure_ascii=False), encoding="utf-8")

    if args.skip_s2:
        log("跳过 SoVITS（用已有权重）")
    else:
        log(f"SoVITS 训练（{args.s2_epochs} epoch / batch {args.batch}）")
        run([PY, "-s", "GPT_SoVITS/s2_train.py", "--config", str(s2_path)], base_env, log_path)

    # ---------- GPT ----------
    import yaml

    s1_cfg = yaml.load(
        (REPO / "GPT_SoVITS" / "configs" / "s1longer-v2.yaml").read_text(encoding="utf-8"),
        Loader=yaml.FullLoader,
    )
    s1_cfg["train"].update(
        {
            "batch_size": args.batch,
            "epochs": args.s1_epochs,
            "save_every_n_epoch": 4,
            "precision": "16-mixed",
            "if_save_every_weights": True,
            "if_save_latest": True,
            "if_dpo": False,
            "half_weights_save_dir": "GPT_weights_v2",
            "exp_name": args.exp,
        }
    )
    s1_cfg.setdefault("data", {})["num_workers"] = 0
    s1_cfg["pretrained_s1"] = str(S1)
    s1_cfg["train_semantic_path"] = str(exp_dir / "6-name2semantic.tsv")
    s1_cfg["train_phoneme_path"] = str(exp_dir / "2-name2text.txt")
    s1_cfg["output_dir"] = str(exp_dir / "logs_s1_v2")
    s1_path = tmp / f"{args.exp}_s1.yaml"
    s1_path.write_text(yaml.dump(s1_cfg, default_flow_style=False), encoding="utf-8")

    log(f"GPT 训练（{args.s1_epochs} epoch / batch {args.batch}）")
    run([PY, "-s", "GPT_SoVITS/s1_train.py", "--config_file", str(s1_path)], base_env, log_path)

    log("完成。产物：")
    for pattern, folder in (("*.pth", "logs_s2_v2"), ("*.ckpt", "logs_s1_v2")):
        found = sorted((exp_dir / folder).glob(pattern))
        log(f"  {folder}: {[p.name for p in found][:4] or '（没找到）'}")
    if (REPO / "SoVITS_weights_v2").exists():
        log(f"  SoVITS_weights_v2: {[p.name for p in (REPO / 'SoVITS_weights_v2').glob('*.pth')][:4]}")
    if (REPO / "GPT_weights_v2").exists():
        log(f"  GPT_weights_v2: {[p.name for p in (REPO / 'GPT_weights_v2').glob('*.ckpt')][:4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
