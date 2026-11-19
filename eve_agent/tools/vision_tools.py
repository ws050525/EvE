"""视觉工具：看图、粗定位。

现在默认走**云端模型**：DeepSeek Flash 本身就收图（`/v1/models` 里
`input_modalities: ["text","image"]`），所以不需要再起本地引擎当"眼睛"。
之前那套本地视觉（Qwen3-VL / UI-TARS）保留成后备，`backend = "local"` 时才用。

三个工具：

- `screen_read(question)`：截屏 → 云端模型描述界面 / 回答具体问题
- `screen_locate(target)`：截屏 → 九宫格选格子 → 给**可点击的候选坐标**（粗定位）
- `image_read(path, question)`：对已有图片提问

为什么定位要绕九宫格：实测直接问像素坐标，云端模型会给到图外面的值
（1082,929 之于 800 高的图）；而"选哪一格"这种分类它答得又快又准
（三题全中、1.2–1.9 秒、3 个输出 token）。代价是**只能粗到 1/3 屏**，
所以定位完必须点一下再 `screen_read` 确认。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from eve_core import vision

from .registry import ToolRegistry, ToolSpec

ProviderGetter = Callable[[], Any]


def _where(shot) -> str:
    return f"活动窗口「{shot.window}」" if shot.window else "整个屏幕"


def _cloud_provider(provider_getter: ProviderGetter | None):
    if provider_getter is None:
        return None
    try:
        return provider_getter()
    except Exception:
        return None


def _screen_read(args: dict[str, Any], runtime, cfg, provider_getter=None) -> str:
    question = str(args.get("question") or "").strip() or (
        "屏幕上现在是什么界面？有哪些窗口、按钮、输入框？关键元素大概在什么位置？"
    )
    try:
        shot = vision.capture(full_screen=bool(args.get("full_screen")))
    except Exception as exc:
        return f"[error] 截图失败：{type(exc).__name__}: {exc}"

    backend = str(getattr(cfg, "vision_backend", "cloud") or "cloud")
    provider = _cloud_provider(provider_getter) if backend != "local" else None

    if provider is not None:
        try:
            answer = vision.cloud_ask(
                provider, shot, question, max_tokens=int(args.get("max_tokens") or 600)
            )
        except Exception as exc:
            return (
                f"[error] 云端看图失败：{type(exc).__name__}: {exc}\n"
                f"（可把 config.toml 的 [vision] backend 改成 \"local\" 用本地模型）"
            )
    else:
        if runtime is None:
            return "[error] 视觉能力没接上（既没有云端 provider，也没有 runtime）"
        try:
            answer = vision.ask(
                runtime,
                cfg,
                shot.path,
                question,
                max_tokens=int(args.get("max_tokens") or 600),
                model_id=str(args.get("model") or ""),
            )
        except Exception as exc:
            return f"[error] 本地看图失败：{type(exc).__name__}: {exc}"

    if not answer:
        return "[error] 视觉模型没有给出内容"
    return f"[ok] 看的是{_where(shot)}（截图 {shot.path}）\n{answer}"


def _screen_locate(args: dict[str, Any], runtime, cfg, provider_getter=None) -> str:
    target = str(args.get("target") or "").strip()
    if not target:
        return "[error] 缺少 target（要找的东西，例如“搜索框”）"

    provider = _cloud_provider(provider_getter)
    if provider is None:
        return "[error] 九宫格定位需要能看图的云端模型；本地模型请用 screen_read 描述后自行判断"

    try:
        shot = vision.capture(full_screen=bool(args.get("full_screen")))
    except Exception as exc:
        return f"[error] 截图失败：{type(exc).__name__}: {exc}"

    try:
        hit = vision.locate_grid(provider, shot, target, levels=1)
    except Exception as exc:
        return f"[error] 定位失败：{type(exc).__name__}: {exc}"

    if not hit.get("found"):
        return f"[error] {_where(shot)}里没找到「{target}」：{hit.get('why') or '看不清'}"

    left, top, right, bottom = hit["rect"]
    # 格心 + 四个内接点：粗定位难免偏，给几个候选比硬点一个位置强
    cx, cy = hit["x"], hit["y"]
    dx, dy = (right - left) // 4, (bottom - top) // 4
    candidates = [(cx, cy), (cx - dx, cy), (cx + dx, cy), (cx, cy - dy), (cx, cy + dy)]

    lines = [
        f"[ok] 「{target}」在{_where(shot)}的这块区域："
        f"x {left}–{right}, y {top}–{bottom}（约 {hit['width']}x{hit['height']} 像素）",
        "候选点击点（按优先级）：" + "、".join(f"({x},{y})" for x, y in candidates),
        f"建议：先 mouse_click{list(candidates[0])}；**点完必须 screen_read 确认状态变了**，"
        f"没变就换下一个候选点，或者改用键盘。",
        f"（截图 {shot.path}）",
    ]
    return "\n".join(lines)


def _image_read(args: dict[str, Any], runtime, cfg, provider_getter=None) -> str:
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

    provider = _cloud_provider(provider_getter)
    if provider is not None:
        try:
            answer = vision.cloud_ask(
                provider, path, question, max_tokens=int(args.get("max_tokens") or 600)
            )
            return f"[ok] {path.name}\n{answer}"
        except Exception as exc:
            return f"[error] 云端看图失败：{type(exc).__name__}: {exc}"

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
        return f"[error] 本地看图失败：{type(exc).__name__}: {exc}"
    return f"[ok] {path.name}\n{answer}"


def register_vision(
    registry: ToolRegistry,
    cfg=None,
    runtime=None,
    provider_getter: ProviderGetter | None = None,
) -> None:
    backend = str(getattr(cfg, "vision_backend", "cloud") or "cloud") if cfg else "cloud"
    route = "云端模型直读图" if backend != "local" else "本地视觉模型"
    hint = f"当前走：{route}"

    registry.register(
        ToolSpec(
            name="screen_read",
            description=(
                "【看图】截当前活动窗口（或整屏），问模型一个关于画面的问题。"
                f"{hint}。"
                "能回答：现在是什么界面、按钮/输入框在哪、操作有没有生效、报错写了什么。"
                "拿不准的时候先看，别瞎点。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "想问什么"},
                    "full_screen": {"type": "boolean", "description": "true = 截整个屏幕"},
                },
            },
            handler=lambda args: _screen_read(args, runtime, cfg, provider_getter),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="screen_locate",
            description=(
                "【粗定位】在画面上找某个东西，返回一块区域和几个候选点击坐标。"
                f"{hint}。"
                "精度是屏幕的九分之一，**只适合输入框、大按钮这类大目标**；"
                "点完一定要 screen_read 确认，没中就换候选点。"
                "小图标不要用它，直接键盘操作。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "要找的东西，用界面上的说法"},
                    "full_screen": {"type": "boolean", "description": "true = 整屏找"},
                },
                "required": ["target"],
            },
            handler=lambda args: _screen_locate(args, runtime, cfg, provider_getter),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="image_read",
            description=(
                "【看图】对一张已有的图片提问（截图、用户给的图）。"
                f"{hint}。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "图片路径"},
                    "question": {"type": "string", "description": "想问什么"},
                },
                "required": ["path"],
            },
            handler=lambda args: _image_read(args, runtime, cfg, provider_getter),
            risk="low",
        )
    )
