"""Windows 自带 OCR：把截图里的文字连位置一起读出来。

为什么需要它：色块检测只能抓"饱和色实心按钮"，纯文字控件、透明按钮、和背景同色
的按钮全都抓不到。但**文字**几乎人人都有，而且 OCR 给的是逐行 bbox，精度 10–15px。

用的是系统自带的 Windows.Media.Ocr（中文系统自带 zh-Hans 引擎），不下载模型、
不装外部程序，一次 200–300ms。

一个坑：低对比度的小字（半透明面板上的灰字）原图认不出来。
所以跑两遍——原图一遍、放大 2 倍加对比度一遍——结果合并去重。
实测这两遍是互补的：原图认出「播放全部」，增强图认出「推荐」。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

MAX_BOXES = 60


def available() -> bool:
    try:
        import winsdk  # noqa: F401
    except Exception:
        return False
    return True


def _engine():
    from winsdk.windows.globalization import Language
    from winsdk.windows.media.ocr import OcrEngine

    engine = None
    for tag in ("zh-Hans", "zh-Hans-CN", "en-US"):
        try:
            engine = OcrEngine.try_create_from_language(Language(tag))
        except Exception:
            engine = None
        if engine is not None:
            break
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    return engine


async def _recognize(path: Path, scale: float = 1.0):
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage import FileAccessMode, StorageFile

    engine = _engine()
    if engine is None:
        return []
    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)

    boxes = []
    for line in result.lines:
        words = line.words
        if not words:
            continue
        text = "".join(word.text for word in words).strip()
        if not text:
            continue
        left = min(word.bounding_rect.x for word in words) / scale
        top = min(word.bounding_rect.y for word in words) / scale
        right = max(word.bounding_rect.x + word.bounding_rect.width for word in words) / scale
        bottom = max(word.bounding_rect.y + word.bounding_rect.height for word in words) / scale
        boxes.append(
            {
                "text": text,
                "box": (int(left), int(top), int(right), int(bottom)),
                "cx": int((left + right) / 2),
                "cy": int((top + bottom) / 2),
            }
        )
    return boxes


def _enhanced_copy(path: Path, folder: Path | None = None) -> Path | None:
    try:
        from PIL import Image, ImageEnhance

        image = Image.open(path).convert("L")
        image = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
        image = ImageEnhance.Contrast(image).enhance(1.8)
        target = ((folder or Path(".tmp") / "vision") / "ocr-enhanced.png").resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return target
    except Exception:
        return None


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # 已经有事件循环在跑（少见）：开个新线程跑完再回
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


def _merge(first: list[dict], second: list[dict]) -> list[dict]:
    """按文字内容 + 位置去重：同一行在两个版本里都会出现。"""
    seen = set()
    merged = []
    for item in first + second:
        key = (item["text"].replace(" ", ""), item["cx"] // 12, item["cy"] // 12)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:MAX_BOXES]


def read_boxes(path: Path, enhanced: bool = True) -> list[dict]:
    """读出图里所有文字行及位置。不可用时返回空列表（调用方自己降级）。"""
    if not available():
        return []
    path = Path(path).resolve()  # Windows 的 StorageFile 不认相对路径（WinError -2147024809）
    try:
        boxes = _run(_recognize(path))
    except Exception:
        return []
    if enhanced:
        prepared = _enhanced_copy(path)
        if prepared is not None:
            try:
                boxes = _merge(boxes, _run(_recognize(prepared, scale=2.0)))
            except Exception:
                pass
    return [b for b in boxes if b["text"]]


def find_text(boxes: list[dict], target: str) -> list[dict]:
    """按关键词粗筛：目标里的字有多少落在这一行里。"""
    target = (target or "").strip()
    if not target:
        return []
    chars = [c for c in target if not c.isspace()]
    scored = []
    for item in boxes:
        text = item["text"].replace(" ", "")
        if target in text:
            scored.append((len(target), item))
            continue
        hit = sum(1 for c in set(chars) if c in text)
        if hit and hit / len(set(chars)) >= 0.6:
            scored.append((hit, item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _score, item in scored]
