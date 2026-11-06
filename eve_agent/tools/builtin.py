"""内置工具：启动程序、列开始菜单、截屏。

`app_launch` 的查找顺序（v0.5.8 起）：
  别名表 → 直接路径 → **开始菜单** → PATH → 给出相近的名字
以前只有 PATH + 绝对路径，所以「打开微信」这类必失败——用户装的程序大多不在 PATH 里。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import apps
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
    "taskmgr": "taskmgr.exe",
    "任务管理器": "taskmgr.exe",
}

LAUNCH_TIMEOUT = 6.0
POLL_INTERVAL = 0.3


def _clean(text: str) -> str:
    """窗口标题里常有零宽字符，清掉再比对。"""
    for ch in ("\u200b", "\u200e", "\u200f", "\ufeff", "\xa0"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


# ---------- 窗口探测（测试里会被替换掉） ----------


def _enum_windows() -> list[dict]:
    from .desktop import _enum_windows as impl

    return impl()


def _force_foreground(hwnd: int) -> bool:
    from .desktop import _force_foreground as impl

    return impl(hwnd)


def _pid_exe(pid: int) -> str:
    from .desktop import _pid_exe as impl

    return impl(pid)


def _wait_new_window(before: set, timeout: float = LAUNCH_TIMEOUT) -> dict | None:
    """等一个"之前没有的"窗口出现。标题还会随后变化，所以读到就再重读一次。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        fresh = [w for w in _enum_windows() if w["hwnd"] not in before and _clean(w["title"]).strip()]
        if fresh:
            # 记事本：'记事本' -> '无标题 - Notepad'，稍等再读，否则交给模型的是过期标题
            time.sleep(0.6)
            refreshed = {w["hwnd"]: w for w in _enum_windows()}
            return refreshed.get(fresh[0]["hwnd"], fresh[0])
    return None


def _already_running(exe_name: str) -> dict | None:
    """同一个 exe 已经在跑，就别再开一个实例。"""
    if not exe_name:
        return None
    for win in _enum_windows():
        try:
            running = os.path.basename(_pid_exe(win["pid"])).lower()
        except Exception:
            continue
        if running and running == exe_name:
            if _force_foreground(win["hwnd"]):
                return win
    return None


def _launch_process(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        argv,
        close_fds=True,
        creationflags=apps.DETACHED_PROCESS,
    )


# ---------- 三条启动路径 ----------


def _launch_executable(cmd: str, resolved: str | None = None) -> str:
    exe_name = os.path.basename(cmd).lower()
    running = _already_running(exe_name)
    if running is not None:
        return (
            f"[ok] {cmd} 已在运行，已激活现有窗口：{_clean(running['title'])!r}，"
            f"hwnd={running['hwnd']}（没有重复启动）"
        )

    before = {w["hwnd"] for w in _enum_windows()}
    try:
        _launch_process([resolved or cmd])
    except OSError as exc:
        return f"[error] 启动失败：{exc}"

    win = _wait_new_window(before)
    if win is None:
        return f"[ok] 已启动 {cmd}，但 {LAUNCH_TIMEOUT:.0f} 秒内没检测到新窗口（可能还在加载或已最小化）"
    return (
        f"[ok] 已启动 {cmd}，窗口标题：{_clean(win['title'])!r}，hwnd={win['hwnd']}。"
        f"下一步用 window_focus(hwnd={win['hwnd']}) 激活它——hwnd 比标题稳，标题可能会变。"
    )


def _launch_from_menu(entry: apps.AppEntry, query: str, others: list[apps.AppEntry]) -> str:
    before = {w["hwnd"] for w in _enum_windows()}
    try:
        launched = apps.launch_entry(entry)
    except OSError as exc:
        return f"[error] 启动失败：{exc}"

    win = _wait_new_window(before)
    head = f"[ok] 已从开始菜单启动 {launched}"
    if entry.name != query:
        head += f"（匹配的是「{entry.name}」）"

    if win is not None:
        head += f"，窗口标题：{_clean(win['title'])!r}，hwnd={win['hwnd']}"
    else:
        head += "，但没检测到新窗口（可能已在运行、或启动较慢）"

    alternatives = [e.name for e in others if e.name != entry.name][:4]
    if alternatives:
        head += f"。开始菜单里相近的还有：{'、'.join(alternatives)}"
    return head


# ---------- 工具入口 ----------


def app_launch(args: dict) -> str:
    target = str(args.get("target", "")).strip()
    if not target:
        return "[error] 缺少 target"

    # 1. 别名：直接对应 exe，最快也最稳
    alias = _ALIASES.get(target.lower())
    if alias:
        resolved = shutil.which(alias)
        if resolved or Path(alias).exists():
            return _launch_executable(alias, resolved)
        # 别名指向的 exe 找不到（比如精简版系统没带画图），继续往下找

    # 2. 用户直接给了路径
    if Path(target).exists():
        return _launch_executable(target, target)

    # 3. 开始菜单：这里是"用户装了但不在 PATH 里"的那些程序
    index = apps.default_index()
    matches = index.find(target, limit=5)
    if matches:
        return _launch_from_menu(matches[0], target, matches)

    # 4. PATH
    resolved = shutil.which(target)
    if resolved:
        return _launch_executable(target, resolved)

    # 5. 都没找到：给几个相近的名字，别让模型自己瞎猜
    hints = index.search(target, limit=5)
    if hints:
        names = "、".join(f"「{e.name}」" for e in hints)
        return f"[error] 找不到「{target}」。开始菜单里相近的有：{names}。请用准确的名字重试。"
    return (
        f"[error] 找不到「{target}」，开始菜单和 PATH 里都没有。"
        f"可以先用 app_list 查一下装了哪些程序（共 {len(index.entries())} 个）。"
    )


def app_list(args: dict) -> str:
    query = str(args.get("query", "") or "").strip()
    try:
        limit = max(1, min(int(args.get("limit", 40) or 40), 80))
    except (TypeError, ValueError):
        limit = 40

    index = apps.default_index()
    entries = index.search(query, limit)
    total = len(index.entries())
    if not entries:
        return f"[ok] 开始菜单里没有匹配「{query}」的程序（共 {total} 个）"

    lines = [f"[ok] {total} 个程序里，{'匹配' if query else ''}{'「' + query + '」的' if query else ''}前 {len(entries)} 个（来源：{index.source}）："]
    for entry in entries:
        lines.append(f"  - {entry.name}")
    if query:
        lines.append("  用 app_launch(target=<上面的名字>) 启动。")
    else:
        lines.append("  名字太多时给 app_list 一个 query 缩小范围，例如 query=微信。")
    return "\n".join(lines)


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
            description=(
                "启动一个程序。target 写用户平时叫的名字就行——中文名（微信、记事本）也可以。"
                "查找顺序：别名 → 路径 → 开始菜单 → PATH。"
                "开始菜单匹配不上时会返回相近的名字，照着那些名字重试。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "程序名（如 微信 / 记事本）或可执行文件路径"}
                },
                "required": ["target"],
            },
            handler=app_launch,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="app_list",
            description=(
                "列出这台机器开始菜单里的程序。不知道某个软件叫什么名字时先用它查，"
                "再用 app_launch 启动。query 可以只写一两个字。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "筛选关键词，留空则列出前若干个"},
                    "limit": {"type": "integer", "description": "最多列多少条，默认 40，上限 80"},
                },
            },
            handler=app_list,
            risk="low",
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
