"""内置工具：v0.1 只放两个最安全的，够验证 desktop 档的链路。

后续 v0.2 再加 key_type / mouse_click / window_focus / uia_find 等。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .registry import ToolRegistry, ToolSpec

TMP_DIR = Path(__file__).resolve().parents[2] / ".tmp"

_ALIASES = {
    "notepad": "notepad.exe",
    "记事本": "notepad.exe",
    "calc": "calc.exe",
    "计算器": "calc.exe",
    "explorer": "explorer.exe",
    "资源管理器": "explorer.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "mspaint": "mspaint.exe",
    "画图": "mspaint.exe",
}


def _clean(text: str) -> str:
    """窗口标题里常有零宽字符，清掉再比对。"""
    for ch in ("\u200b", "\u200e", "\u200f", "\ufeff", "\xa0"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


def app_launch(args: dict) -> str:
    target = str(args.get("target", "")).strip()
    if not target:
        return "[error] 缺少 target"

    cmd = _ALIASES.get(target.lower(), target)

    resolved = shutil.which(cmd)
    if resolved is None and not Path(cmd).exists():
        return f"[error] 找不到可执行文件：{target}"

    # 先记下现有窗口，启动后靠"新增窗口"确认它真的起来了
    from .desktop import _enum_windows, _force_foreground, _pid_exe

    # 已经在跑就别再开一个实例，直接激活已有窗口
    exe_name = os.path.basename(cmd).lower()
    for win in _enum_windows():
        running = os.path.basename(_pid_exe(win["pid"])).lower()
        if running and running == exe_name:
            if _force_foreground(win["hwnd"]):
                return (
                    f"[ok] {cmd} 已在运行，已激活现有窗口：{_clean(win['title'])!r}，"
                    f"hwnd={win['hwnd']}（没有重复启动）"
                )
            break

    before = {w["hwnd"] for w in _enum_windows()}

    try:
        proc = subprocess.Popen(
            [resolved or cmd],
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as exc:
        return f"[error] 启动失败：{exc}"

    deadline = time.time() + 6.0
    while time.time() < deadline:
        time.sleep(0.3)
        fresh = [
            w for w in _enum_windows()
            if w["hwnd"] not in before and _clean(w["title"]).strip()
        ]
        if fresh:
            same_pid = [w for w in fresh if w["pid"] == proc.pid]
            win = (same_pid or fresh)[0]
            # 窗口标题在创建后还会变（记事本：'记事本' -> '无标题 - Notepad'）。
            # 稍等再读一次，否则交给模型的是过期标题，它按标题找窗口就会失败。
            time.sleep(0.6)
            refreshed = {w["hwnd"]: w for w in _enum_windows()}
            win = refreshed.get(win["hwnd"], win)
            return (
                f"[ok] 已启动 {cmd}，窗口标题：{_clean(win['title'])!r}，hwnd={win['hwnd']}。"
                f"下一步用 window_focus(hwnd={win['hwnd']}) 激活它——hwnd 比标题稳，标题可能会变。"
            )

    return f"[ok] 已启动 {cmd}，但 6 秒内没检测到新窗口（可能还在加载或已最小化）"


def screen_capture(args: dict) -> str:
    try:
        from PIL import ImageGrab
    except ImportError:
        return "[error] 缺少 Pillow，无法截图"

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"screen-{datetime.now():%Y%m%d-%H%M%S}.png"
    try:
        img = ImageGrab.grab(all_screens=True)
    except Exception as exc:
        return f"[error] 截图失败：{exc}"
    img.save(path)
    return f"[ok] 已截图 {path}（{img.width}x{img.height}）"


def register_builtin(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="app_launch",
            description="启动一个本地应用程序。target 可以是常见别名（notepad/calc/explorer）或可执行文件路径。",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "程序别名或可执行文件路径"}
                },
                "required": ["target"],
            },
            handler=app_launch,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="screen_capture",
            description="抓取当前屏幕并保存为 PNG，返回文件路径。用于需要看界面才能决定的下一步。",
            parameters={"type": "object", "properties": {}},
            handler=screen_capture,
            risk="low",
        )
    )
