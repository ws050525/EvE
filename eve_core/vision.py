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
import io
from datetime import datetime
from pathlib import Path

MAX_SIDE = 1600  # 送进模型的图先缩到这个尺寸，省 token 也更快
MAX_BYTES = 4 * 1024 * 1024


def capture(path: Path | None = None) -> Path:
    """截全屏（多显示器也一起），存成 png 并返回路径。"""
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow，无法截图") from exc

    image = ImageGrab.grab(all_screens=True)
    if max(image.width, image.height) > MAX_SIDE:
        ratio = MAX_SIDE / float(max(image.width, image.height))
        image = image.resize((int(image.width * ratio), int(image.height * ratio)))

    if path is None:
        folder = Path(".tmp") / "vision"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"screen-{datetime.now():%Y%m%d-%H%M%S}.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def pick_vision_model(cfg, preferred: str = "") -> str:
    """挑一个带视觉投影的模型；没有就返回空串。"""
    models = getattr(cfg, "models", None) or {}
    if preferred and preferred in models:
        return preferred
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


def _prepare(path: Path, max_side: int = MAX_SIDE) -> tuple[str, float]:
    """把图缩到 max_side 以内并编码，返回 (data_url, 缩放比例)。

    缩放比例是给坐标定位用的：模型说的是"图上的像素"，除以比例才是屏幕坐标。
    """
    from PIL import Image

    image = Image.open(path).convert("RGB")
    longest = max(image.width, image.height)
    scale = 1.0
    if longest > max_side:
        scale = max_side / float(longest)
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded, scale


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
    image_url, _scale = _prepare(Path(image_path))
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


def locate(
    runtime, cfg, image_path: Path, target: str, model_id: str = ""
) -> dict:
    """问"某个东西在屏幕哪个坐标"，返回 {found, x, y, what}（坐标已换算成屏幕坐标）。"""
    model_id = pick_vision_model(cfg, model_id)
    if not model_id:
        raise RuntimeError("没有可用的视觉模型（[models.*] 里要有 mmproj 的 GGUF）")

    provider = runtime.provider_for(model_id)
    image_url, scale = _prepare(Path(image_path))
    messages = [
        {
            "role": "system",
            "content": "你是屏幕定位器。只输出 JSON。坐标必须是图上真实可见元素的像素位置。",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": LOCATE_PROMPT.format(target=target)},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]
    result = provider.chat(messages, None, max_tokens=200, temperature=0.0)
    text = (result.content or "").strip()
    data = _extract_json(text)
    if not data:
        return {"found": False, "x": 0, "y": 0, "what": f"看不懂模型的回答：{text[:80]}", "raw": text}

    found = bool(data.get("found"))
    try:
        x = float(data.get("x") or 0)
        y = float(data.get("y") or 0)
    except (TypeError, ValueError):
        x = y = 0.0
    # 图被缩过：换算回真实屏幕坐标
    if scale and scale != 1.0:
        x = x / scale
        y = y / scale
    return {
        "found": found and (x > 0 or y > 0),
        "x": int(round(x)),
        "y": int(round(y)),
        "what": str(data.get("what") or ""),
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
