"""OCR 模块的测试：文字匹配、去重合并、不可用时的降级。

不依赖网络；Windows OCR 不可用时相关用例自动跳过（打印 skip）。
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_core import ocr

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


def box(text, cx, cy):
    return {"text": text, "box": (cx - 10, cy - 5, cx + 10, cy + 5), "cx": cx, "cy": cy}


@case
def exact_match_wins_over_partial():
    boxes = [box("下载", 100, 50), box("播放全部", 400, 270), box("播放", 700, 900)]
    hits = ocr.find_text(boxes, "播放全部")
    assert hits and hits[0]["text"] == "播放全部", hits


@case
def partial_character_overlap_is_accepted():
    """OCR 常把小字认糊（"播客"→"间播客"），允许按字重叠匹配。"""
    boxes = [box("间播客", 100, 300), box("我喜欢的音乐", 100, 500)]
    hits = ocr.find_text(boxes, "播客")
    assert hits and hits[0]["text"] == "间播客", hits


@case
def unrelated_text_is_filtered_out():
    boxes = [box("下载", 100, 50), box("最近播放", 100, 600)]
    assert ocr.find_text(boxes, "音量图标") == []


@case
def merge_drops_duplicates_from_the_two_passes():
    first = [box("播放全部", 400, 270), box("下载", 580, 270)]
    second = [box("播放全部", 401, 271), box("推荐", 110, 160)]
    merged = ocr._merge(first, second)
    texts = [item["text"] for item in merged]
    assert texts.count("播放全部") == 1, texts
    assert "推荐" in texts and "下载" in texts, texts


@case
def merge_caps_the_number_of_boxes():
    many = [box(f"行{i}", 10 * i, 10 * i) for i in range(200)]
    assert len(ocr._merge(many, [])) <= ocr.MAX_BOXES


@case
def missing_engine_returns_empty_not_crash():
    original = ocr.available
    ocr.available = lambda: False
    try:
        assert ocr.read_boxes(Path("nope.png")) == []
    finally:
        ocr.available = original


@case
def real_ocr_reads_a_rendered_label():
    """真跑一次系统 OCR：画一张带大字的图，看能不能读出来。"""
    if not ocr.available():
        print("     （本机没有 winsdk，跳过）")
        return
    from PIL import Image, ImageDraw

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "label.png"
        image = Image.new("RGB", (600, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle([40, 40, 300, 120], fill=(230, 70, 70))
        draw.text((70, 65), "PLAY ALL", fill="white")
        image.save(path)

        boxes = ocr.read_boxes(path, enhanced=False)
        assert boxes, "OCR 一行都没读出来"
        text = " ".join(item["text"] for item in boxes).upper()
        assert "PLAY" in text or "ALL" in text, text


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
