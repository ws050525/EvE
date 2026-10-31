"""桌面操作工具。

设计原则（与 docs/design-mvp.md §11.6 一致）：
1. 优先用系统接口（窗口枚举/激活、程序启动），不确定就退到键鼠注入。
2. 视觉定位是最后手段——截图一次约 11 秒，只在该用时用。
3. 每个动作都可记录、可回放；高风险动作由档位的 require_confirm 兜底。

依赖：pyautogui（已装）、ctypes（标准库）、tkinter（标准库，用于中文粘贴）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from typing import Any

from .registry import ToolRegistry, ToolSpec

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010


def _pid_exe(pid: int) -> str:
    """取进程可执行文件全路径，失败返回空串。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


# ---------- 窗口（纯 ctypes，不引三方库）----------


def _enum_windows() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"hwnd": int(hwnd), "title": buf.value, "pid": int(pid.value)})
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return found


def _match_windows(query: str) -> list[dict[str, Any]]:
    query = (query or "").strip().lower()
    windows = _enum_windows()
    if not query:
        return windows
    return [w for w in windows if query in w["title"].lower()]


def window_list(args: dict[str, Any]) -> str:
    windows = _match_windows(args.get("filter", ""))
    if not windows:
        return "[ok] 没有匹配的窗口。可以先不带 filter 调用一次看看全部窗口。"
    lines = [f"{w['hwnd']}\t{w['title']}" for w in windows[:30]]
    return "[ok] 可见窗口（hwnd\\t标题）：\n" + "\n".join(lines)


def _force_foreground(hwnd: int) -> bool:
    """Windows 默认禁止后台进程抢焦点，这里用 attach 线程的常规办法绕一下。"""
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.1)
    if user32.SetForegroundWindow(hwnd):
        return True

    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    if fg_thread and target_thread and fg_thread != target_thread:
        user32.AttachThreadInput(fg_thread, target_thread, True)
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
        user32.AttachThreadInput(fg_thread, target_thread, False)
        if ok:
            return True
    return bool(user32.SetForegroundWindow(hwnd))


def window_focus(args: dict[str, Any]) -> str:
    hwnd = args.get("hwnd")
    title = str(args.get("title", "")).strip()

    if hwnd:
        target = int(hwnd)
    elif title:
        matches = _match_windows(title)
        if not matches:
            return f"[error] 没有标题包含 '{title}' 的窗口"
        target = matches[0]["hwnd"]
    else:
        return "[error] 需要 hwnd 或 title 之一"

    if not _force_foreground(target):
        return (
            "[error] 无法激活该窗口（系统拒绝前台切换）。"
            "可以让用户手动点一下，或用 key_hotkey('alt+tab') 换过去"
        )
    time.sleep(0.2)
    return f"[ok] 已激活 hwnd={target}"


def window_close(args: dict[str, Any]) -> str:
    hwnd = args.get("hwnd")
    title = str(args.get("title", "")).strip()
    if hwnd:
        target = int(hwnd)
    elif title:
        matches = _match_windows(title)
        if not matches:
            return f"[error] 没有标题包含 '{title}' 的窗口"
        target = matches[0]["hwnd"]
    else:
        return "[error] 需要 hwnd 或 title 之一"

    if not user32.PostMessageW(target, WM_CLOSE, 0, 0):
        return f"[error] 无法向 hwnd={target} 发送关闭消息"
    time.sleep(0.35)
    if user32.IsWindow(target):
        return (
            f"[ok] 已向 hwnd={target} 发送关闭请求，但窗口仍在。"
            "多半弹出了「是否保存」对话框，需要用户处理"
        )
    return f"[ok] 窗口 hwnd={target} 已关闭"


# ---------- 键鼠注入 ----------


def _require_pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True  # 鼠标甩到左上角即中止，保命
    pyautogui.PAUSE = 0.05
    return pyautogui


def _paste_text(text: str) -> None:
    """中文无法用 pyautogui.write 直接输入，走剪贴板 + Ctrl+V。"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    time.sleep(0.05)
    root.destroy()


def key_type(args: dict[str, Any]) -> str:
    text = str(args.get("text", ""))
    if not text:
        return "[error] 缺少 text"

    gui = _require_pyautogui()
    if text.isascii():
        gui.write(text, interval=0.01)
    else:
        _paste_text(text)
        time.sleep(0.1)
        gui.hotkey("ctrl", "v")
    return f"[ok] 已输入 {len(text)} 个字符"


def key_hotkey(args: dict[str, Any]) -> str:
    combo = str(args.get("keys", "")).strip().lower()
    if not combo:
        return "[error] 缺少 keys，例如 ctrl+s"
    parts = [p.strip() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return "[error] keys 格式不对，例如 ctrl+s"
    gui = _require_pyautogui()
    gui.hotkey(*parts)
    return f"[ok] 已按下 {'+'.join(parts)}"


def mouse_click(args: dict[str, Any]) -> str:
    try:
        x = int(args["x"])
        y = int(args["y"])
    except (KeyError, TypeError, ValueError):
        return "[error] 需要整数 x 和 y"
    clicks = int(args.get("clicks", 1) or 1)
    if not (0 <= x <= 20000 and 0 <= y <= 20000):
        return f"[error] 坐标不合理：({x}, {y})"

    gui = _require_pyautogui()
    gui.click(x=x, y=y, clicks=max(1, min(clicks, 3)), interval=0.15)
    return f"[ok] 已点击 ({x}, {y}) x{clicks}"


def register_desktop(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="window_list",
            description="列出当前可见的窗口（hwnd 与标题）。在动手之前先用它确认目标窗口存在。",
            parameters={
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "标题包含的关键词，可留空"}
                },
            },
            handler=window_list,
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="window_focus",
            description="把指定窗口切到前台。优先用标题关键词匹配；hwnd 更精确。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题关键词"},
                    "hwnd": {"type": "integer", "description": "窗口句柄（window_list 返回）"},
                },
            },
            handler=window_focus,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="window_close",
            description="关闭指定窗口（发送 WM_CLOSE）。窗口有未保存内容时会弹出系统对话框。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题关键词"},
                    "hwnd": {"type": "integer", "description": "窗口句柄"},
                },
            },
            handler=window_close,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="key_type",
            description="向当前焦点窗口输入文本（中英文都支持，中文走剪贴板粘贴）。",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "要输入的内容"}},
                "required": ["text"],
            },
            handler=key_type,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="key_hotkey",
            description="按下组合键，例如 ctrl+s、alt+tab、win+r、enter。",
            parameters={
                "type": "object",
                "properties": {"keys": {"type": "string", "description": "用 + 连接，例如 ctrl+s"}},
                "required": ["keys"],
            },
            handler=key_hotkey,
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="mouse_click",
            description="在绝对屏幕坐标点击。坐标只应来自截图分析结果，不要凭感觉猜。",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "clicks": {"type": "integer", "description": "点击次数，默认 1"},
                },
                "required": ["x", "y"],
            },
            handler=mouse_click,
            risk="high",
        )
    )
