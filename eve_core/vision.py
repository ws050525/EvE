"""让看不了图的模型也能"看屏幕"：用本地 Qwen3-VL 当眼睛。

背景：干活的档位现在跑云端模型（DeepSeek Flash），它不收图片；而桌面操作
（"点搜索框""这一页的下拉在哪"）本质上需要看。本地那个 Qwen3-VL 有视觉，
但工具调用能力弱，不适合当主控。

所以分工：**云端模型下命令，本地视觉模型负责描述屏幕**。
`screen_read("网易云音乐的搜索框在哪")` 会截屏 → 问本地视觉模型 → 返回文字。
主控拿到文字再决定点哪儿。

代价：会把本地引擎拉起来（约 4–5GB 显存）。所以这是个"按需启动"的能力，
不用就不占。引擎已经起着的话（聊天档）直接复用。
"""

from __future__ import annotations

import base64
import ctypes
import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAX_SIDE = 1600  # 最长边上限
MAX_PIXELS = 900_000  # 总像素上限：视觉模型的时间和 token 都跟它走
MAX_BYTES = 4 * 1024 * 1024
MIN_CROP = 240  # 裁出来的窗口小于这个尺寸就不用它（大概是误判）

# 只截活动窗口时，图比全屏小得多，模型看得更准也更快。
# DPI 必须说清楚：不说的话 GetWindowRect 给逻辑像素、ImageGrab 给物理像素，
# 缩放屏上会裁偏。
try:  # pragma: no cover - 只在 Windows 上生效
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


@dataclass
class Shot:
    """一张截图 + 它在屏幕上的位置（locate 换算坐标要用）。"""

    path: Path
    origin: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)
    window: str = ""

    @property
    def full_screen(self) -> bool:
        return not self.window


def _rect_of(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口的可见边框（优先 DWM 的，去掉了不可见的阴影边）。"""
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    try:
        dwm = ctypes.windll.dwmapi
        # DWMWA_EXTENDED_FRAME_BOUNDS = 9
        if dwm.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(9), ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0 and rect.right > rect.left:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass

    try:
        if ctypes.windll.user32.GetWindowRect(
            ctypes.c_void_p(hwnd), ctypes.byref(rect)
        ):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


def foreground_window() -> tuple[int, str, tuple[int, int, int, int]] | None:
    """当前前台窗口：(hwnd, 标题, 矩形)。拿不到返回 None。"""
    try:
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
    except Exception:
        return None
    if not hwnd:
        return None

    length = 0
    buffer = None
    try:
        length = int(user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd)))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, length + 1)
    except Exception:
        buffer = None
    title = (buffer.value if buffer else "") or ""

    bounds = _rect_of(hwnd)
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    if right - left < MIN_CROP or bottom - top < MIN_CROP:
        return None
    # 桌面/任务栏这类壳窗口没有意义，当成"没窗口"处理，退回全屏
    if title.strip() in {"Program Manager", "Windows 输入体验", ""}:
        return None
    return hwnd, title, (left, top, right, bottom)


def _virtual_origin() -> tuple[int, int]:
    """虚拟桌面左上角（多显示器时可能是负数）。"""
    try:
        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
            int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
        )
    except Exception:
        return 0, 0


def window_info(hwnd: int) -> tuple[str, tuple[int, int, int, int]] | None:
    """指定窗口的 (标题, 矩形)。窗口不存在、最小化、太小都返回 None。"""
    try:
        user32 = ctypes.windll.user32
        if not hwnd or not user32.IsWindow(ctypes.c_void_p(int(hwnd))):
            return None
        if user32.IsIconic(ctypes.c_void_p(int(hwnd))):
            return None  # 最小化时截图是空的，退回前台窗口
        length = int(user32.GetWindowTextLengthW(ctypes.c_void_p(int(hwnd))))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buffer, length + 1)
        title = buffer.value or ""
    except Exception:
        return None
    bounds = _rect_of(int(hwnd))
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    if right - left < MIN_CROP or bottom - top < MIN_CROP:
        return None
    return title, (left, top, right, bottom)


def capture(
    path: Path | None = None,
    full_screen: bool = False,
    folder: Path | None = None,
    hwnd: int = 0,
) -> Shot:
    """截一张图。

    默认截 `hwnd` 指定的窗口（agent 正在操作的那个），
    没指定就截前台窗口；都拿不到才退回全屏。
    """
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow，无法截图") from exc

    image = ImageGrab.grab(all_screens=True)
    base_origin = _virtual_origin()
    origin = base_origin
    window_title = ""

    found = None if full_screen else window_info(int(hwnd or 0))
    if found is None and not full_screen:
        foreground = foreground_window()  # (hwnd, title, rect)
        if foreground is not None:
            found = (foreground[1], foreground[2])
    if found is not None:
        title, (left, top, right, bottom) = found
        box = (
            max(0, left - base_origin[0]),
            max(0, top - base_origin[1]),
            min(image.width, right - base_origin[0]),
            min(image.height, bottom - base_origin[1]),
        )
        if box[2] - box[0] >= MIN_CROP and box[3] - box[1] >= MIN_CROP:
            image = image.crop(box)
            # 裁出来的图，左上角对应的屏幕坐标
            origin = (box[0] + base_origin[0], box[1] + base_origin[1])
            window_title = title

    # 存原图，不在这里缩：缩了以后"图上的像素"就对不上屏幕像素，
    # locate() 算出的坐标会整体偏小（踩过：点哪儿都落在左上角一带）。
    # 真正送模型前由 _prepare() 缩放，并把缩放比交给坐标换算。
    if path is None:
        base = Path(folder) if folder else Path(".tmp") / "vision"
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 同一秒内可能截好几张（读一次、定位一次），加随机后缀免得互相覆盖
        path = base / f"shot-{stamp}-{uuid.uuid4().hex[:4]}.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)

    return Shot(
        path=path,
        origin=origin,
        size=(image.width, image.height),
        window=window_title,
    )


MIN_MODEL_MB = 20  # 太小的多半是没下完的半截文件


def _model_ready(model, cfg) -> bool:
    """权重和视觉投影都在磁盘上、且大小像完整的，才算能用。

    配置里给了 `weights_mb` / `mmproj_mb` 就按它卡 95%；没给就只挡掉明显是空壳的。
    """
    weights = getattr(model, "path", "")
    mmproj = getattr(model, "mmproj", "")
    if not weights or not mmproj:
        return False
    try:
        root = Path(getattr(cfg, "root", "."))
        checks = (
            (weights, int(getattr(model, "weights_mb", 0) or 0)),
            (mmproj, int(getattr(model, "mmproj_mb", 0) or 0)),
        )
        for item, expected in checks:
            path = Path(item)
            if not path.is_absolute():
                path = root / path
            if not path.exists():
                return False
            size_mb = path.stat().st_size / 1048576.0
            floor = expected * 0.95 if expected else MIN_MODEL_MB
            if size_mb < floor:
                return False  # 下到一半的别用，加载会直接崩
    except Exception:
        return False
    return True


def pick_vision_model(cfg, preferred: str = "") -> str:
    """挑一个能用的视觉模型：优先指定的，其次第一个文件齐全的。

    没下载完的模型会被跳过——所以可以"配置里先写好新模型、下好了自动生效、
    没下好还能用旧的"，不用来回改配置。
    """
    models = getattr(cfg, "models", None) or {}
    if preferred and preferred in models and _model_ready(models[preferred], cfg):
        return preferred
    for model_id, model in models.items():
        if getattr(model, "mmproj", "") and _model_ready(model, cfg):
            return model_id
    # 一个都跑不了：把配了视觉的那个名字报回去，让报错信息具体一点
    for model_id, model in models.items():
        if getattr(model, "mmproj", ""):
            return model_id
    return ""


def _data_url(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise RuntimeError(
            f"图片太大（{len(raw) / 1048576:.1f}MB），先缩小或裁剪再问"
        )
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _fit(image, max_side: int = MAX_SIDE, max_pixels: int = MAX_PIXELS):
    """按最长边和总像素两个上限缩放。视觉模型的时间基本跟像素数成正比。"""
    scale = 1.0
    longest = max(image.width, image.height)
    if longest > max_side:
        scale = max_side / float(longest)
    pixels = image.width * image.height
    if pixels and pixels * scale * scale > max_pixels:
        scale = min(scale, (max_pixels / float(pixels)) ** 0.5)
    if scale >= 0.999:
        return image, 1.0
    return image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale)))), scale


def _prepare(path: Path, max_side: int = MAX_SIDE) -> tuple[str, float, tuple[int, int]]:
    """把图缩到上限内并编码，返回 (data_url, 缩放比例, 送出去的图尺寸)。"""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    image, scale = _fit(image, max_side=max_side)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded, scale, (image.width, image.height)


# ---------------------------------------------------------------------------
# 云端视觉：主模型本身就能看图时，直接问它，不用另起引擎
# ---------------------------------------------------------------------------

GRID_COLS = 3
GRID_ROWS = 3
FAST = {"thinking": {"type": "disabled"}, "effort": "low"}  # 找东西不需要深度思考


def _encode(image, quality: int = 88) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _chat_vision(provider, data_url: str, question: str, max_tokens: int = 200) -> str:
    """把一张图 + 一个问题发给云端模型。参数不被支持时退回只有图的那版。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    try:
        result = provider.chat(messages, None, max_tokens=max_tokens, temperature=0.0, extra=FAST)
    except Exception:
        # 有的服务不认 thinking/effort，去掉重试一次
        result = provider.chat(messages, None, max_tokens=max_tokens, temperature=0.0)
    return (result.content or "").strip()


def cloud_ask(provider, shot, question: str, max_tokens: int = 400) -> str:
    """直接让云端模型回答一个关于截图的问题。`shot` 可以是 Shot 或路径。"""
    from PIL import Image

    path = shot.path if isinstance(shot, Shot) else Path(shot)
    image, _scale = _fit(Image.open(path).convert("RGB"))
    return _chat_vision(provider, _encode(image), question, max_tokens=max_tokens)


def _draw_grid(image, cols: int = GRID_COLS, rows: int = GRID_ROWS):
    from PIL import ImageDraw

    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    width, height = image.width / cols, image.height / rows
    for row in range(rows):
        for col in range(cols):
            x0, y0 = col * width, row * height
            draw.rectangle([x0, y0, x0 + width - 1, y0 + height - 1], outline=(255, 0, 255), width=3)
            draw.text((x0 + 8, y0 + 6), str(row * cols + col + 1), fill=(255, 0, 255))
    return marked


def _ask_cell(provider, image, target: str, cols: int = GRID_COLS, rows: int = GRID_ROWS) -> tuple[int, int] | None:
    """让模型说目标落在第几格，返回 (row, col)。分类比回归可靠得多。"""
    question = (
        f"图被 {cols}x{rows} 网格切成 1-{cols * rows}（1 左上、{cols} 右上、"
        f"{cols * (rows - 1) + 1} 左下、{cols * rows} 右下）。"
        f"「{target}」的中心在哪个格子？只输出一行：答案=N"
    )
    answer = _chat_vision(provider, _encode(_draw_grid(image, cols, rows)), question, max_tokens=30)
    numbers = re.findall(r"[1-9]", answer)
    if not numbers:
        return None
    cell = int(numbers[-1])
    if not 1 <= cell <= cols * rows:
        return None
    return (cell - 1) // cols, (cell - 1) % cols


def locate_grid(provider, shot, target: str, levels: int = 1) -> dict:
    """九宫格定位：先大格、再逐级细化，返回**屏幕坐标**的矩形和中心点。

    为什么不直接问像素坐标：实测云端模型给的 y 会超出图高（1082,929 之于 800 高的图），
    而"选哪一格"这种分类问题它答得又快又准（3/3、1.2–1.9 秒、3 个输出 token）。
    """
    from PIL import Image

    path = shot.path if isinstance(shot, Shot) else Path(shot)
    origin = shot.origin if isinstance(shot, Shot) else (0, 0)
    image, scale = _fit(Image.open(path).convert("RGB"))

    box = (0, 0, image.width, image.height)
    cell_rect = box
    for level in range(max(1, levels)):
        crop = image.crop(box)
        cell = _ask_cell(provider, crop, target)
        if cell is None:
            return {"found": False, "why": "模型没给出格子", "target": target}
        row, col = cell
        # 最后一级就停在这一格；中间级往外扩 45%，避免目标被切在边界上
        margin = 0.45 if level < levels - 1 else 0.0
        width, height = crop.width / GRID_COLS, crop.height / GRID_ROWS
        left = max(0, int(box[0] + col * width - margin * width))
        top = max(0, int(box[1] + row * height - margin * height))
        right = min(image.width, int(box[0] + (col + 1) * width + margin * width))
        bottom = min(image.height, int(box[1] + (row + 1) * height + margin * height))
        box = (left, top, right, bottom)
        cell_rect = box

    # 缩放后的图 → 截图原像素 → 屏幕坐标
    def to_screen(value: float, offset: int) -> int:
        return int(round(value / scale + offset))

    left, top, right, bottom = cell_rect
    screen = (
        to_screen(left, origin[0]),
        to_screen(top, origin[1]),
        to_screen(right, origin[0]),
        to_screen(bottom, origin[1]),
    )
    return {
        "found": True,
        "target": target,
        "rect": screen,
        "x": (screen[0] + screen[2]) // 2,
        "y": (screen[1] + screen[3]) // 2,
        "width": screen[2] - screen[0],
        "height": screen[3] - screen[1],
        "levels": levels,
    }


def ask(
    runtime,
    cfg,
    image_path: Path,
    question: str,
    max_tokens: int = 600,
    model_id: str = "",
) -> str:
    """拿一张图 + 一个问题去问本地视觉模型。"""
    model_id = pick_vision_model(cfg, model_id)
    if not model_id:
        raise RuntimeError("没有可用的视觉模型（[models.*] 里要有 mmproj 的 GGUF）")

    provider = runtime.provider_for(model_id)
    image_url, _scale, _size = _prepare(Path(image_path))
    messages = [
        {
            "role": "system",
            "content": "你在看一张屏幕截图。只描述你真正看到的东西，不要猜。"
            "回答尽量短，涉及位置时说清大概在哪个区域（比如'顶部中间'）。",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]
    result = provider.chat(
        messages, None, max_tokens=max_tokens, temperature=0.2
    )
    return (result.content or "").strip()


LOCATE_PROMPT = """在这张屏幕截图里找：{target}

只回一个 JSON，不要别的：
{{"found": true/false, "x": 图中像素x, "y": 图中像素y, "what": "你看到的那个东西是什么"}}

要求：
- 坐标取该元素**中心点**在图上的像素坐标（左上角是 0,0）；
- 找不到就 found=false，坐标给 0；
- 不确定也要给"最像的"那个，不要编造不存在的元素。"""


# UI-TARS / Holo 这类"GUI 专用"模型不吃"回个 JSON"那套，它们按自己的动作格式输出，
# 坐标是 0–1000 的归一化值。所以提示词和解析要分两套。
UITARS_PROMPT = """Target: {target}

你是 GUI agent。请给出点击这个目标的操作，只输出一行：
Action: click(start_box='<|box_start|>(x,y)<|box_end|>')

x、y 用 0–1000 的归一化坐标（左上角 0,0，右下角 1000,1000），取目标中心点。
找不到就只输出：Action: fail"""

_UI_TARS_COORD = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)")


def model_format(cfg, model_id: str = "", override: str = "") -> str:
    """判断该用哪套坐标格式：general（JSON 像素）还是 uitars（动作 + 归一化）。"""
    if override in ("general", "uitars"):
        return override
    models = getattr(cfg, "models", None) or {}
    model = models.get(model_id)
    text = (model_id + " " + str(getattr(model, "path", "") if model else "")).lower()
    if any(key in text for key in ("ui-tars", "uitars", "holo")):
        return "uitars"
    return "general"


def locate(
    runtime, cfg, shot, target: str, model_id: str = "", fmt: str = ""
) -> dict:
    """问"某个东西在屏幕哪个坐标"，返回 {found, x, y, what}。

    `shot` 可以是 Shot（裁活动窗口时带 origin），也可以是纯路径（按全屏算）。
    坐标最后换算成**真实屏幕坐标**：还原缩放、按需重建归一化坐标、再加裁剪原点。
    """
    if isinstance(shot, Shot):
        image_path, origin = shot.path, shot.origin
    else:
        image_path, origin = Path(shot), (0, 0)

    model_id = pick_vision_model(cfg, model_id)
    if not model_id:
        raise RuntimeError("没有可用的视觉模型（[models.*] 里要有 mmproj 的 GGUF）")

    fmt = model_format(cfg, model_id, fmt or getattr(cfg, "vision_format", ""))
    provider = runtime.provider_for(model_id)
    image_url, scale, (sent_w, sent_h) = _prepare(Path(image_path))
    original_w = sent_w / scale if scale else sent_w
    original_h = sent_h / scale if scale else sent_h

    if fmt == "uitars":
        system = "你是 GUI agent。严格按要求的动作格式输出，不要解释。"
        question = UITARS_PROMPT.format(target=target)
        max_tokens = 120
    else:
        system = "你是屏幕定位器。只输出 JSON。坐标必须是图上真实可见元素的像素位置。"
        question = LOCATE_PROMPT.format(target=target)
        max_tokens = 200

    result = provider.chat(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        None,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    text = (result.content or "").strip()

    if fmt == "uitars":
        return _parse_uitars(text, scale, (original_w, original_h), origin)

    data = _extract_json(text)
    if not data:
        return {"found": False, "x": 0, "y": 0, "what": f"看不懂模型的回答：{text[:80]}", "raw": text}

    found = bool(data.get("found"))
    try:
        x = float(data.get("x") or 0)
        y = float(data.get("y") or 0)
    except (TypeError, ValueError):
        x = y = 0.0
    # 图被缩过：先还原到"截图里的像素"
    if scale and scale != 1.0:
        x = x / scale
        y = y / scale
    # 截图是裁出来的：再加回左上角的屏幕坐标
    x += origin[0]
    y += origin[1]
    return {
        "found": found and (x > 0 or y > 0),
        "x": int(round(x)),
        "y": int(round(y)),
        "what": str(data.get("what") or ""),
        "raw": text,
    }


def _parse_uitars(
    text: str, scale: float, size: tuple[float, float], origin: tuple[int, int]
) -> dict:
    """解析 UI-TARS 的 `Action: click(start_box='<|box_start|>(x,y)<|box_end|>')`。

    坐标有两种写法：0–1000 归一化（UI-TARS 的习惯）或者直接给像素（有些转换版）。
    大于 1000 的按像素处理，否则按归一化算。
    """
    if "fail" in text.lower() and not _UI_TARS_COORD.search(text):
        return {"found": False, "x": 0, "y": 0, "what": "模型说找不到", "raw": text}

    match = _UI_TARS_COORD.search(text)
    if not match:
        return {"found": False, "x": 0, "y": 0, "what": f"看不懂的动作：{text[:80]}", "raw": text}

    x = float(match.group(1))
    y = float(match.group(2))
    width, height = size
    if x <= 1000 and y <= 1000:
        # 归一化坐标 → 原始图像像素
        px = x / 1000.0 * width
        py = y / 1000.0 * height
    else:
        # 已经是"送出去的图"的像素
        px = x / scale if scale else x
        py = y / scale if scale else y

    return {
        "found": True,
        "x": int(round(px + origin[0])),
        "y": int(round(py + origin[1])),
        "what": "gui-model",
        "raw": text,
    }


def _extract_json(text: str) -> dict:
    """从模型回复里抠出第一个 JSON 对象（它经常前后加废话）。"""
    import json
    import re

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def to_jpeg_bytes(image_path: Path, max_side: int = MAX_SIDE) -> bytes:
    """备用：把图转成 jpeg 字节（有些服务更认 jpeg）。"""
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    if max(image.width, image.height) > max_side:
        ratio = max_side / float(max(image.width, image.height))
        image = image.resize((int(image.width * ratio), int(image.height * ratio)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()
