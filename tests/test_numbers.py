"""数字读法的测试：中文数字转换、日期时间、以及进播报前的清洗。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_core.numbers import digits_to_zh, int_to_zh, normalize_numbers
from eve_core.tts import clean_for_speech

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def integers_use_chinese_units():
    assert int_to_zh(0) == "零"
    assert int_to_zh(5) == "五"
    assert int_to_zh(10) == "十", int_to_zh(10)
    assert int_to_zh(13) == "十三", "十位开头不写一"
    assert int_to_zh(20) == "二十"
    assert int_to_zh(101) == "一百零一"
    assert int_to_zh(1005) == "一千零五"
    assert int_to_zh(2026) == "二千零二十六"
    assert int_to_zh(10000) == "一万"
    assert int_to_zh(100500) == "十万零五百", int_to_zh(100500)
    assert int_to_zh(100000001) == "一亿零一", int_to_zh(100000001)
    assert int_to_zh(12345678) == "一千二百三十四万五千六百七十八"


@case
def long_numbers_are_read_digit_by_digit():
    assert normalize_numbers("13800138000") == "一三八零零一三八零零零"
    assert digits_to_zh("2026") == "二零二六"


@case
def dates_and_times():
    assert normalize_numbers("2026年") == "二零二六年"
    assert normalize_numbers("2026-11-22") == "二零二六年十一月二十二日"
    assert normalize_numbers("11月22日") == "十一月二十二日"
    assert normalize_numbers("15:30") == "十五点三十分"
    assert normalize_numbers("9:05") == "九点零五分", normalize_numbers("9:05")


@case
def money_percent_ordinal():
    assert normalize_numbers("50%") == "百分之五十"
    assert normalize_numbers("¥128") == "一百二十八元"
    assert normalize_numbers("100万") == "一百万"
    assert normalize_numbers("第3名") == "第三名"
    assert normalize_numbers("3.14") == "三点一四"
    assert normalize_numbers("1-2个") == "一到二个"


@case
def clean_for_speech_turns_numbers_into_chinese():
    """CosyVoice 的文本前端没装，数字不转就会被念成英文。"""
    assert "二零二六" in clean_for_speech("今天是2026年11月22日。")
    assert "二十五" in clean_for_speech("我有25个文件。")
    assert "百分之五十" in clean_for_speech("完成了50%。")
    assert "三点一四" in clean_for_speech("圆周率是3.14。")


@case
def clean_for_speech_drops_filenames():
    spoken = clean_for_speech("我把 report_2026.xlsx 删了")
    assert "xlsx" not in spoken
    assert "文件" in spoken


@case
def markdown_and_kaomoji_still_cleaned():
    assert "*" not in clean_for_speech("**好的**，马上做")
    assert "ω" not in clean_for_speech("在。说吧，要干啥(・ω・)。")
    assert "```" not in clean_for_speech("看这个：\n```python\nprint(1)\n```\n就这样")



@case
def urls_lose_protocol_and_say_three_w():
    """播报网址：不念协议头，www 说成"三w"，路径查询串不念。"""
    spoken = clean_for_speech("打开 https://www.baidu.com 就行")
    assert "https" not in spoken, spoken
    assert "三w点baidu点com" in spoken, spoken
    # 域名里的数字逐位念，点也要念出来
    assert clean_for_speech("参考 http://163.com/a?b=1 这个") == "参考 一六三点com 这个"
    assert "三w" not in clean_for_speech("ww.example.com 和 w3.org")
    assert "三w" not in clean_for_speech("只有一个 w 的 ws 不算")



@case
def speech_cleaning_never_touches_display_text():
    """播报用的清洗只能作用在"要念的文本"上，界面显示必须原样。

    用户明确要求：聊天里显示 `www.baidu.com`，只有念的时候说"三w"。
    这条测试扫源码，确保 clean_for_speech 只在语音合成模块里被调用。
    """
    root = Path(__file__).resolve().parents[1]
    allowed = {"eve_core/tts.py", "eve_core/tts_cosy.py"}
    offenders = []
    for path in list((root / "eve_ui").glob("*.py")) + list((root / "eve_agent").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "clean_for_speech" in text:
            offenders.append(rel)
    assert not offenders, f"这些地方不该调用播报清洗：{offenders}"


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # 意外异常同样算失败
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    total = len(CHECKS)
    print(f"\n{total - failed}/{total} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
