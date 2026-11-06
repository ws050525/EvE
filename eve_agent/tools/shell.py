"""work 档的命令行工具：shell_exec。

它是最有用的工具，也是最危险的。两道防线：

1. **底线黑名单**——关机、格式化、删盘、改注册表启动项这类一次性毁掉机器的命令，
   无论模型怎么说、用户怎么确认，一律拒绝。对应 design-persona 里"底线不可被覆盖"。
2. **用户确认**——风险等级 high，work 档 require_confirm=true，一定弹窗。

其余的交给人判断：命令原文会一字不改地展示给用户。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec

DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 600.0
OUTPUT_CHARS = 6000

# 匹配到就拒绝执行。宁可误杀，也不让"清盘""关机"这类命令真的跑起来。
_DENY = [
    (re.compile(r"(?i)\bshutdown(\.exe)?\b"), "关机 / 重启"),
    (re.compile(r"(?i)\b(logoff|poweroff|reboot)\b"), "关机 / 重启"),
    (re.compile(r"(?i)\bformat(\.com)?\s+[a-z]:"), "格式化磁盘"),
    (re.compile(r"(?i)\bdiskpart\b"), "磁盘分区工具"),
    (re.compile(r"(?i)\bmkfs\b"), "格式化磁盘"),
    (re.compile(r"(?i)\bdel\s+/[sfq]\b|\brd\s+/s\b|\brmdir\s+/s\b"), "递归强制删除"),
    (re.compile(r"(?i)\brm\s+-[a-z]*[rf]"), "递归强制删除"),
    (re.compile(r"(?i)\breg\s+delete\b"), "删除注册表项"),
    (re.compile(r"(?i)\bbcdedit\b"), "修改启动项"),
    (re.compile(r"(?i)\bvssadmin\b"), "卷影副本操作"),
    (re.compile(r"(?i)\bcipher\s+/w"), "擦除磁盘空闲空间"),
    (re.compile(r"(?i)Remove-Item[^\n]*-Recurse"), "递归删除"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork 炸弹"),
]


def blocked_reason(command: str) -> str:
    for pattern, reason in _DENY:
        if pattern.search(command):
            return reason
    return ""


def _decode(raw: bytes) -> str:
    """Windows 控制台默认 cp936，但也可能是 UTF-8。先按 UTF-8 试，坏了再退。"""
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _root_of(cfg) -> Path:
    root = getattr(cfg, "work_root", None) if cfg is not None else None
    path = Path(root) if root else Path(__file__).resolve().parents[2]
    return path.resolve()


def _clip(text: str, limit: int = OUTPUT_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（输出过长，已省略 {len(text) - limit} 字符）"


def shell_exec(args: dict[str, Any], root: Path) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "[error] 缺少 command"

    reason = blocked_reason(command)
    if reason:
        return f"[denied] 这条命令命中底线黑名单（{reason}），不会执行：{command}"

    try:
        timeout = float(args.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = min(max(5.0, timeout), MAX_TIMEOUT)

    cwd = root
    raw_cwd = str(args.get("cwd", "") or "").strip()
    if raw_cwd:
        candidate = Path(raw_cwd)
        cwd = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            cwd.relative_to(root)
        except ValueError:
            return f"[error] 拒绝执行：cwd {cwd} 不在工作目录 {root} 之内"
        if not cwd.is_dir():
            return f"[error] cwd 不存在或是文件：{cwd}"

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return f"[error] 命令超时（{timeout:.0f}s）：{command}"
    except OSError as exc:
        return f"[error] 启动失败：{exc}"

    stdout = _clip(_decode(proc.stdout))
    stderr = _clip(_decode(proc.stderr))
    head = f"[ok] exit={proc.returncode}  cwd={cwd}"

    parts = [head]
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"--- stderr ---\n{stderr}")
    if proc.returncode != 0 and not stdout and not stderr:
        parts.append("（没有任何输出）")
    return "\n".join(parts)


def register_shell(registry: ToolRegistry, cfg=None) -> None:
    root = _root_of(cfg)
    registry.register(
        ToolSpec(
            name="shell_exec",
            description=(
                f"在工作目录 {root} 下执行一条命令（Windows cmd 语法），返回退出码和输出。"
                "用于跑测试、看 git 状态、批量处理文件。"
                "一次只跑一条命令；会改动文件的命令先说明意图。"
                "关机、格式化、递归强删这类命令会被直接拒绝。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "完整命令，例如 python tests\\test_persona.py"},
                    "cwd": {"type": "string", "description": "工作目录（默认工作目录根，不能越界）"},
                    "timeout": {
                        "type": "number",
                        "description": f"超时秒数，默认 {DEFAULT_TIMEOUT:.0f}，上限 {MAX_TIMEOUT:.0f}",
                    },
                },
                "required": ["command"],
            },
            handler=lambda args: shell_exec(args, root),
            risk="high",
        )
    )
