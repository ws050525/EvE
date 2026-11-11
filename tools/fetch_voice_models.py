"""下载语音模型（识别 / 断句）。模型不进版本库，只下到 models/voice/。

    & 'D:\\python\\python.exe' tools\\fetch_voice_models.py            # SenseVoice + silero VAD
    & 'D:\\python\\python.exe' tools\\fetch_voice_models.py --asr      # 只下识别模型
    & 'D:\\python\\python.exe' tools\\fetch_voice_models.py --vad      # 只下断句模型（2MB）
    & 'D:\\python\\python.exe' tools\\fetch_voice_models.py --list     # 看下什么、下到哪、有没有

为什么要换识别模型：原来用的是 paraformer-zh-small（2024-03），小模型对长句、
口音、数字和标点的处理都偏弱。SenseVoice（2024-07）是同一个官方的模型库，
中文准确率明显更好，还自带标点和数字规整，CPU 上跑一句话大约零点几秒。

GitHub 直连经常超时，所以默认走 ghproxy 镜像；不通再退回直连。
"""

from __future__ import annotations

import argparse
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "models" / "voice"

GH = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
MIRROR = "https://ghproxy.net/"

# 名字 → (远程文件名, 目标路径, 大致大小)
ASR_ARCHIVE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"
ASR_DIR = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
VAD_FILE = "silero_vad.onnx"


def _urls(name: str) -> list[str]:
    """镜像优先；镜像挂了再直连。"""
    return [f"{MIRROR}{GH}/{name}", f"{GH}/{name}"]


def _download(url: str, target: Path, expect_mb: float = 0.0) -> bool:
    print(f"下载 {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "eve-fetch/1.0", "Accept": "*/*"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            last = 0.0
            with temp.open("wb") as handle:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 1.0:
                        last = now
                        if total > 0:
                            print(
                                f"  {done / 1048576:6.1f} / {total / 1048576:6.1f} MB"
                                f"  {done * 100 / total:5.1f}%"
                            )
                        else:
                            print(f"  {done / 1048576:6.1f} MB")
        if total > 0 and done < total:
            print(f"  没下完：{done} / {total} 字节")
            temp.unlink(missing_ok=True)
            return False
        if expect_mb and done < expect_mb * 1048576 * 0.5:
            print(f"  文件太小（{done / 1048576:.1f} MB），大概是被拦了")
            temp.unlink(missing_ok=True)
            return False
        temp.replace(target)
        print(f"  完成：{target}（{target.stat().st_size / 1048576:.1f} MB）")
        return True
    except Exception as exc:
        print(f"  失败：{type(exc).__name__}: {exc}")
        temp.unlink(missing_ok=True)
        return False


def fetch_url(name: str, target: Path, expect_mb: float = 0.0) -> bool:
    for url in _urls(name):
        if _download(url, target, expect_mb):
            return True
        print("  换下一个源…")
    return False


def fetch_asr() -> bool:
    final = VOICE_DIR / ASR_DIR
    if (final / "tokens.txt").exists() and any(final.glob("model*.onnx")):
        print(f"识别模型已存在：{final}")
        return True

    archive = VOICE_DIR / ASR_ARCHIVE
    if not archive.exists():
        if not fetch_url(ASR_ARCHIVE, archive, expect_mb=200):
            return False

    print(f"解压 {archive.name}")
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(VOICE_DIR)
    except Exception as exc:
        print(f"  解压失败：{type(exc).__name__}: {exc}")
        return False
    archive.unlink(missing_ok=True)

    if not (final / "tokens.txt").exists():
        print(f"解压后没找到 {final}")
        return False
    print(f"识别模型就绪：{final}")
    return True


def fetch_vad() -> bool:
    target = VOICE_DIR / VAD_FILE
    if target.exists() and target.stat().st_size > 500_000:
        print(f"断句模型已存在：{target}")
        return True
    return fetch_url(VAD_FILE, target, expect_mb=1)


def show() -> int:
    print(f"模型目录：{VOICE_DIR}")
    if not VOICE_DIR.is_dir():
        print("  （还不存在）")
        return 1

    asr = VOICE_DIR / ASR_DIR
    print(f"  SenseVoice 识别   {'有' if (asr / 'tokens.txt').exists() else '没有'}"
          f"   {asr.name}")
    para = VOICE_DIR / "sherpa-onnx-paraformer-zh-small-2024-03-09"
    print(f"  paraformer 回退   {'有' if para.is_dir() else '没有'}   {para.name}")
    vad = VOICE_DIR / VAD_FILE
    print(f"  silero 断句       {'有' if vad.exists() else '没有'}   {vad.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_voice_models", description="下载语音模型")
    parser.add_argument("--asr", action="store_true", help="只下识别模型（压缩包 ≈156MB，解开 228MB）")
    parser.add_argument("--vad", action="store_true", help="只下 silero 断句模型（≈2MB）")
    parser.add_argument("--list", action="store_true", help="只看状态，不下载")
    args = parser.parse_args(argv)

    if args.list:
        return show()

    both = not (args.asr or args.vad)
    want_asr = args.asr or both
    want_vad = args.vad or both

    ok = True
    if want_asr:
        ok = fetch_asr() and ok
    if want_vad:
        ok = fetch_vad() and ok

    print()
    show()
    if not ok:
        print("\n有东西没下成。可以重跑本脚本（已下载的会跳过），或者手动去 GitHub releases 下。")
        return 1
    print("\n完成。config.toml 里 asr_model 已经指向 SenseVoice，直接能用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
