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

import re
from pathlib import Path
from typing import Any, Callable

from eve_core import ocr, vision

from . import desktop as desktop_tools
from .registry import ToolRegistry, ToolSpec

ProviderGetter = Callable[[], Any]


def _where(shot) -> str:
    return f"活动窗口「{shot.window}」" if shot.window else "整个屏幕"


def _target_hwnd() -> int:
    """截图优先截 agent 正在操作的那个窗口，而不是碰巧在前台的那个。"""
    try:
        return int(desktop_tools.get_target()[0])
    except Exception:
        return 0


def _cloud_provider(provider_getter: ProviderGetter | None):
    if provider_getter is None:
        return None
    try:
        return provider_getter()
    except Exception:
        return None


def _pick_from_text(provider, target: str, items: list[dict]) -> dict | None:
    """把 OCR 认出来的文字列成菜单，让模型选编号（纯文字请求，很便宜）。"""
    menu = "\n".join(f"{index}. {item['text'][:40]}" for index, item in enumerate(items, 1))
    question = (
        f"界面上识别到这些文字：\n{menu}\n\n"
        f"用户想找的是「{target}」。哪一行最可能是它？"
        "只输出一个数字编号；都不是就输出 0。"
    )
    try:
        result = provider.chat(
            [{"role": "user", "content": question}],
            None,
            max_tokens=10,
            temperature=0.0,
            extra=vision.FAST,
        )
    except Exception:
        return None
    digits = re.findall(r"\d+", (result.content or "").strip())
    if not digits:
        return None
    choice = int(digits[0])
    if 1 <= choice <= len(items):
        return items[choice - 1]
    return None


def _pick_from_boxes(provider, shot, target: str, boxes: list[dict]) -> dict | None:
    """给候选框画编号，让模型看一眼选（框已经精确，模型只做语义匹配）。"""
    from PIL import Image, ImageDraw

    image = Image.open(shot.path).convert("RGB")
    draw = ImageDraw.Draw(image)
    for index, item in enumerate(boxes, 1):
        x0, y0, x1, y1 = item["box"]
        local = (x0 - shot.origin[0], y0 - shot.origin[1], x1 - shot.origin[0], y1 - shot.origin[1])
        draw.rectangle(local, outline=(0, 255, 0), width=4)
        draw.text((local[0] + 6, max(0, local[1] - 14)), f"#{index}", fill=(0, 255, 0))
    question = (
        f"绿色框是候选控件（共 {len(boxes)} 个）。哪个是「{target}」？"
        "只输出编号，例如 #2；都不是就输出 0。"
    )
    try:
        answer = vision.cloud_ask(provider, image, question, max_tokens=10)
    except Exception:
        return None
    digits = re.findall(r"\d+", answer or "")
    if not digits:
        return None
    choice = int(digits[0])
    if 1 <= choice <= len(boxes):
        return boxes[choice - 1]
    return None


def _ui_find(args: dict[str, Any], runtime, cfg, provider_getter=None) -> str:
    target = str(args.get("target") or "").strip()
    if not target:
        return "[error] 缺少 target（要找的东西）"
    provider = _cloud_provider(provider_getter)
    if provider is None:
        return "[error] ui_find 需要能看图的云端模型"

    try:
        shot = vision.capture(full_screen=bool(args.get("full_screen")), hwnd=_target_hwnd())
    except Exception as exc:
        return f"[error] 截图失败：{type(exc).__name__}: {exc}"

    where = _where(shot)
    lines: list[str] = []

    # 第一层：OCR 读文字（对"只有文字不一样"的控件最有效，精度 10–15px）
    boxes = []
    try:
        boxes = ocr.read_boxes(shot.path)
    except Exception:
        boxes = []
    if boxes:
        hits = ocr.find_text(boxes, target)
        picked = None
        if len(hits) == 1:
            picked = hits[0]
        elif len(hits) > 1:
            picked = _pick_from_text(provider, target, hits[:8])
        if picked:
            return (
                f"[ok] 「{target}」在 {picked['text']!r} 这里："
                f"x {picked['box'][0]}–{picked['box'][2]}, y {picked['box'][1]}–{picked['box'][3]}"
                f"（{where}，靠 OCR 认字找到的，精度约 10px）\n"
                f"点击中心：mouse_click({picked['cx']}, {picked['cy']})\n"
                f"点完用 screen_read 确认，没中就换 ui_find 的下一层（把 target 换个说法再试）。"
            )
        lines.append(f"OCR 找到了 {len(boxes)} 行文字，但没有和「{target}」对得上的")

    # 第二层：饱和色实心块（纯色按钮、色块图标）
    blocks = []
    try:
        blocks = vision.color_blocks(shot)
    except Exception:
        blocks = []
    if len(blocks) == 1:
        chosen = blocks[0]
    elif len(blocks) > 1:
        chosen = _pick_from_boxes(provider, shot, target, blocks)
    else:
        chosen = None
        lines.append("也没有找到饱和色块")
    if chosen:
        return (
            f"[ok] 「{target}」大概是这块：x {chosen['box'][0]}–{chosen['box'][2]}, "
            f"y {chosen['box'][1]}–{chosen['box'][3]}"
            f"（{where}，靠色块检测找到的）\n"
            f"点击中心：mouse_click({chosen['x']}, {chosen['y']})\n"
            f"点完用 screen_read 确认；不对就换下一层。"
        )

    # 第三层：九宫格粗定位（无字、无纯色的图形控件）
    try:
        hit = vision.locate_grid(provider, shot, target, levels=1)
    except Exception as exc:
        return "[error] 定位失败：" + f"{type(exc).__name__}: {exc}\n" + "；".join(lines)
    if not hit.get("found"):
        return (
            f"[error] 三种办法都没找到「{target}」：{'；'.join(lines) or '无'}\n"
            "换个说法再找一次，或者直接用键盘（Tab / 快捷键）。"
        )
    left, top, right, bottom = hit["rect"]
    return (
        f"[ok] 只能粗定到这块区域：x {left}–{right}, y {top}–{bottom}"
        f"（{where}，九宫格兜底，精度只有 1/3 屏）\n"
        f"候选点：({hit['x']},{hit['y']})" + "；前面两种办法没结果：" + "；".join(lines) + "\n"
        "先 mouse_click 格心，点完一定 screen_read 确认；点不中就换键盘。"
    )


def _screen_read(args: dict[str, Any], runtime, cfg, provider_getter=None) -> str:
    question = str(args.get("question") or "").strip() or (
        "屏幕上现在是什么界面？有哪些窗口、按钮、输入框？关键元素大概在什么位置？"
    )
    try:
        shot = vision.capture(full_screen=bool(args.get("full_screen")), hwnd=_target_hwnd())
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
        shot = vision.capture(full_screen=bool(args.get("full_screen")), hwnd=_target_hwnd())
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
            name="ui_find",
            description=(
                "【定位首选】在界面上找控件，**优先用这个**。三层降级："
                "① OCR 认字（10–15px，绝大多数按钮输入框都有文字）"
                "② 饱和色块（纯色按钮、色块图标）"
                "③ 九宫格兜底（纯图形，精度只有 1/3 屏）。"
                f"{hint}。"
                "返回可点击坐标；点完必须 screen_read 确认，没中就把说法换一下再 ui_find 一次。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "要找的控件，优先写它**上面显示的文字**，例如“播放全部”",
                    },
                    "full_screen": {"type": "boolean", "description": "true = 整屏找"},
                },
                "required": ["target"],
            },
            handler=lambda args: _ui_find(args, runtime, cfg, provider_getter),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="screen_locate",
            description=(
                "【粗定位】九宫格，只给一块区域和几个候选点，精度约 1/3 屏。"
                f"{hint}。"
                "**有文字或有色块的控件优先用 ui_find**；这个留给纯图形控件。"
                "点完一定要 screen_read 确认。"
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
