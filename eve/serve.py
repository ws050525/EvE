"""启动本地推理服务（llama-server），读 config.toml 的 [engine] 段。

之所以用 Python 而不是 .ps1：这台机器禁止未签名脚本直接执行，Python 没有这个限制。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ConfigError, EngineConfig, load_config


def _healthy(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("status") == "ok"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return False


def build_command(engine: EngineConfig) -> list[str]:
    cmd = [
        engine.bin,
        "-m", engine.model,
        "-ngl", str(engine.ngl),
        "-c", str(engine.ctx),
        "-t", str(engine.threads),
        "--port", str(engine.port),
        "--host", "127.0.0.1",
    ]
    if engine.mmproj:
        cmd += ["--mmproj", engine.mmproj]
    cmd += list(engine.extra_args)
    return cmd


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = load_config()
    engine = cfg.engine
    if engine is None:
        print("config.toml 里没有 [engine] 段，无法启动本地服务", file=sys.stderr)
        return 2

    if _healthy(engine.port):
        print(f"本地服务已经在 127.0.0.1:{engine.port} 运行，无需重复启动")
        return 0

    for label, value in (("engine bin", engine.bin), ("model", engine.model), ("mmproj", engine.mmproj)):
        if value and not Path(value).exists():
            print(f"{label} 不存在：{value}", file=sys.stderr)
            return 2

    cmd = build_command(engine)
    print("启动：" + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    try:
        proc = subprocess.Popen(cmd)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"服务进程提前退出（code={proc.returncode}）", file=sys.stderr)
            return 1
        if _healthy(engine.port):
            print(f"就绪：http://127.0.0.1:{engine.port}/v1  （Ctrl+C 停止）")
            break
        time.sleep(0.5)
    else:
        print("等待服务就绪超时（120s）", file=sys.stderr)
        proc.terminate()
        return 1

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n停止中…")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
