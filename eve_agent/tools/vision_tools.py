"""视觉工具：让云端主控能"看"屏幕和图片。

两个工具：

- `screen_read(question)`：截屏后问本地视觉模型，返回文字描述。
- `image_read(path, question)`：对已有的图（比如 browser_screenshot 存的）发问。

为什么不是直接给主控塞图片：DeepSeek Flash 不收图。所以让本地的 Qwen3-VL 当眼睛，
主控拿文字做决策。这样"看"和"动手"各用各的长处。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eve_core import vision

from .registry import ToolRegistry, ToolSpec


def _screen_read(args: dict[str, Any], runtime, cfg) -> str:
    question = str(args.get("question") or "").strip() or (
        "屏幕上现在是什么界面？有没有搜索框、输入框、按钮？大概在什么位置？"
    )
    if runtime is None:
        return "[error] 视觉能力没接上（缺少 runtime）"
    try:
        path = vision.capture()
    except Exception as exc:
        return f"[error] 截图失败：{type(exc).__name__}: {exc}"
    try:
        answer = vision.ask(
            runtime,
            cfg,
            path,
            question,
            max_tokens=int(args.get("max_tokens") or 600),
            model_id=str(args.get("model") or ""),
        )
    except Exception as exc:
        return f"[error] 看图失败：{type(exc).__name__}: {exc}"
    if not answer:
        return "[error] 视觉模型没有给出内容"
    return f"[ok] 截图：{path}\n{answer}"


def _image_read(args: dict[str, Any], runtime, cfg) -> str:
    raw = str(args.get("path") or "").strip()
    if not raw:
        return "[error] 缺少 path"
    path = Path(raw)
    if not path.is_absolute():
        root = getattr(cfg, "work_root", None)
        path = (Path(root) / path) if root else path
    if not path.exists():
        return f"[error] 图片不存在：{path}"
    question = str(args.get("question") or "").strip() or "这张图里有什么？"
    if runtime is None:
        return "[error] 视觉能力没接上（缺少 runtime）"
    try:
        answer = vision.ask(
            runtime,
            cfg,
            path,
            question,
            max_tokens=int(args.get("max_tokens") or 600),
            model_id=str(args.get("model") or ""),
        )
    except Exception as exc:
        return f"[error] 看图失败：{type(exc).__name__}: {exc}"
    return f"[ok] {path.name}\n{answer}"


def _screen_locate(args: dict[str, Any], runtime, cfg) -> str:
    target = str(args.get("target") or "").strip()
    if not target:
        return "[error] 缺少 target（要找的东西，例如“搜索框”）"
    if runtime is None:
        return "[error] 视觉能力没接上（缺少 runtime）"
    try:
        path = vision.capture()
    except Exception as exc:
        return f"[error] 截图失败：{type(exc).__name__}: {exc}"
    try:
        hit = vision.locate(
            runtime, cfg, path, target, model_id=str(args.get("model") or "")
        )
    except Exception as exc:
        return f"[error] 定位失败：{type(exc).__name__}: {exc}"
    if not hit.get("found"):
        return f"[error] 屏幕上没找到「{target}」：{hit.get('what') or '看不清'}"
    return (
        f"[ok] 「{target}」在 ({hit['x']}, {hit['y']})——{hit.get('what') or '已定位'}\n"
        f"下一步可以直接 mouse_click({hit['x']}, {hit['y']})；"
        f"位置不对就再 locate 一次或先 screen_read 看看整个界面。"
    )


def register_vision(registry: ToolRegistry, cfg=None, runtime=None) -> None:
    model = ""
    if cfg is not None:
        try:
            model = vision.pick_vision_model(cfg, getattr(cfg, "vision_model", ""))
        except Exception:
            model = ""
    hint = f"当前视觉模型：{model}" if model else "当前没有可用的视觉模型"

    registry.register(
        ToolSpec(
            name="screen_read",
            description=(
                "【看图】截一张全屏图，让本地视觉模型描述界面（按钮/输入框在哪、现在是什么页面）。"
                f"{hint}。"
                "要操作桌面软件但你不知道界面长什么样时，先调它；"
                "拿到描述再用 mouse_click / key_type 动手。"
                "第一次调用会把本地视觉引擎拉起来，等几秒。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "你想知道什么。例如：搜索框在哪个位置？页面中间显示什么？",
                    }
                },
            },
            handler=lambda args: _screen_read(args, runtime, cfg),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="screen_locate",
            description=(
                "【看图定位】在屏幕上找某个东西，直接给**可点击的屏幕坐标**。"
                f"{hint}。"
                "要找“搜索框”“播放按钮”“登录按钮”这类具体控件时用它，"
                "拿到坐标再 mouse_click —— 比让模型猜坐标靠谱得多。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "要找的东西，用界面上看得见的说法，例如“搜索框”“播放按钮”",
                    }
                },
                "required": ["target"],
            },
            handler=lambda args: _screen_locate(args, runtime, cfg),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="image_read",
            description=(
                "【看图】对一张已有的图片提问（browser_screenshot 存的截图、用户给的图）。"
                f"{hint}。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "图片路径（相对工作目录或绝对路径）"},
                    "question": {"type": "string", "description": "想问什么"},
                },
                "required": ["path"],
            },
            handler=lambda args: _image_read(args, runtime, cfg),
            risk="low",
        )
    )
