"""把阿拉伯数字转成中文读法，给播报用。

为什么需要：CosyVoice 的文本前端（ttsfrd）没装，`123` 这类数字它处理不好，
实测会**直接用英文念**（"one two three"）或者干脆跳过。
所以在送进合成器之前先转成中文。

规则尽量贴近中文口语：

| 输入 | 输出 |
| --- | --- |
| `2026年` | 二零二六年（年份按位念） |
| `2026-11-22` | 二零二六年十一月二十二日 |
| `15:30` | 十五点三十分 |
| `3.14` | 三点一四 |
| `50%` | 百分之五十 |
| `¥128` | 一百二十八元 |
| `100万` | 一百万 |
| `13800138000` | 一三八零零一三八零零零（长数字逐位） |
| `第3` | 第三 |
| `1-2` | 一到二 |
"""

from __future__ import annotations

import re

DIGITS = "零一二三四五六七八九"
UNITS = ("", "十", "百", "千")
BIG_UNITS = ("", "万", "亿", "万亿")

# 电话号码 / 身份证 / 订单号这类长串：逐位念最自然
LONG_DIGITS = 7


def _four(n: int) -> str:
    """0–9999 的中文读法（不带"万"以上的单位）。"""
    if n == 0:
        return ""
    out: list[str] = []
    zero = False
    for index, unit in enumerate(("千", "百", "十", "")):
        digit = (n // (10 ** (3 - index))) % 10
        if digit == 0:
            if out and not zero:
                zero = True
            continue
        if zero and out:
            out.append("零")
            zero = False
        out.append(DIGITS[digit] + unit)
    return "".join(out)


def int_to_zh(n: int) -> str:
    """整数转中文：2026 → 二千零二十六，10000 → 一万。"""
    n = int(n)
    if n == 0:
        return "零"
    if n < 0:
        return "负" + int_to_zh(-n)

    groups: list[int] = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000

    out = ""
    for index in range(len(groups) - 1, -1, -1):
        block = groups[index]
        if block == 0:
            if out and not out.endswith("零"):
                out += "零"
            continue
        # 高位的块不满千（例如 10万 → "一十万"），中间要补零
        if out and block < 1000 and not out.endswith("零"):
            out += "零"
        out += _four(block) + BIG_UNITS[index]

    out = re.sub("零+", "零", out).strip("零") or "零"
    # 十位开头的习惯写法：13 → 十三，不是 一十三
    if out.startswith("一十"):
        out = out[1:]
    return out


def digits_to_zh(text: str) -> str:
    """逐位念：13800138000 → 一三八零零一三八零零零。"""
    return "".join(DIGITS[int(ch)] if ch.isdigit() else ch for ch in text)


def _read_number(token: str) -> str:
    """普通数值（可能带小数点）：读成中文。"""
    if "." in token:
        whole, frac = token.split(".", 1)
        whole_text = int_to_zh(int(whole)) if whole else "零"
        return whole_text + "点" + digits_to_zh(frac)
    if len(token) >= LONG_DIGITS:
        return digits_to_zh(token)
    return int_to_zh(int(token))


def _read_year(token: str) -> str:
    """年份逐位念：2026 → 二零二六。"""
    return digits_to_zh(token)


# ---------- 正则 ----------

_RE_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_RE_DATE_DASH = re.compile(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
_RE_DATE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_RE_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?(?!\d)")
_RE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RE_MONEY = re.compile(r"[¥￥$]\s*(\d+(?:\.\d+)?)\s*(万|亿)?")
_RE_ORDINAL = re.compile(r"第\s*(\d+)")
_RE_YEAR = re.compile(r"(?<!\d)(\d{4})\s*年")
_RE_RANGE = re.compile(r"(?<=\d)\s*[-~～]\s*(?=\d)")
_RE_WAN = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)")
_RE_DECIMAL = re.compile(r"(?<!\d)(\d+\.\d+)(?!\d)")
_RE_INT = re.compile(r"(?<!\d)(\d+)(?!\d)")


def normalize_numbers(text: str) -> str:
    """把文本里的阿拉伯数字换成中文读法。"""
    text = str(text or "")
    if not text:
        return text

    # 1) 完整日期（先处理，避免被后面的规则拆散）
    text = _RE_DATE_CN.sub(
        lambda m: f"{_read_year(m.group(1))}年{int_to_zh(int(m.group(2)))}月"
        f"{int_to_zh(int(m.group(3)))}日",
        text,
    )
    text = _RE_DATE_DASH.sub(
        lambda m: f"{_read_year(m.group(1))}年{int_to_zh(int(m.group(2)))}月"
        f"{int_to_zh(int(m.group(3)))}日",
        text,
    )
    text = _RE_DATE_MD.sub(
        lambda m: f"{int_to_zh(int(m.group(1)))}月{int_to_zh(int(m.group(2)))}日",
        text,
    )

    # 2) 时间
    def _time(m: re.Match) -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        out = f"{int_to_zh(hour)}点"
        if minute:
            # 9:05 念"九点零五分"，不是"九点五分"
            out += ("零" + DIGITS[minute] + "分") if minute < 10 else f"{int_to_zh(minute)}分"
        if m.group(3):
            second = int(m.group(3))
            if second:
                out += f"{int_to_zh(second)}秒"
        return out

    text = _RE_TIME.sub(_time, text)

    # 3) 百分比、钱、序数
    text = _RE_PERCENT.sub(lambda m: "百分之" + _read_number(m.group(1)), text)
    text = _RE_MONEY.sub(
        lambda m: _read_number(m.group(1)) + (m.group(2) or "") + "元", text
    )
    text = _RE_ORDINAL.sub(lambda m: "第" + _read_number(m.group(1)), text)

    # 4) 年份（只跟"年"的 4 位数）
    text = _RE_YEAR.sub(lambda m: _read_year(m.group(1)) + "年", text)

    # 5) 数字区间：1-2 → 一到二
    text = _RE_RANGE.sub("到", text)

    # 6) 万/亿 这种"数值 + 单位"的写法
    text = _RE_WAN.sub(lambda m: int_to_zh(int(float(m.group(1)))) + m.group(2), text)

    # 7) 小数、整数
    text = _RE_DECIMAL.sub(lambda m: _read_number(m.group(1)), text)
    text = _RE_INT.sub(lambda m: _read_number(m.group(1)), text)
    return text
