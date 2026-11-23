"""把阿拉伯数字转成"能念出来的"文字。

两条路线：

- **中文语境**：`2026` → 二零二六，`50%` → 百分之五十
- **英文语境**：`123` → one hundred twenty-three，`50%` → fifty percent

为什么要分语境：以前不分，全英文的回复里数字也被转成"一百二十三"，
中文 TTS 念出来是一串怪音，英文 TTS 又读不出中文——两头不讨好。

判断依据看数字**前后 16 个字符**：拉丁字母多就是英文语境，汉字多就是中文语境。
一句话里中英混排时，各自按自己那一侧算，比"整段统一"更准。
"""

from __future__ import annotations

import re

# ==================== 中文 ====================

DIGITS = "零一二三四五六七八九"
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
        if out and block < 1000 and not out.endswith("零"):
            out += "零"
        out += _four(block) + BIG_UNITS[index]

    out = re.sub("零+", "零", out).strip("零") or "零"
    if out.startswith("一十"):
        out = out[1:]
    return out


def digits_to_zh(text: str) -> str:
    """逐位念：13800138000 → 一三八零零一三八零零零。"""
    return "".join(DIGITS[int(ch)] if ch.isdigit() else ch for ch in text)


def _read_number_zh(token: str) -> str:
    if "." in token:
        whole, frac = token.split(".", 1)
        whole_text = int_to_zh(int(whole)) if whole else "零"
        return whole_text + "点" + digits_to_zh(frac)
    if len(token) >= LONG_DIGITS:
        return digits_to_zh(token)
    return int_to_zh(int(token))


def _read_year_zh(token: str) -> str:
    return digits_to_zh(token)


# ==================== 英文 ====================

_EN_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
)
_EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_EN_BIG = ("", "thousand", "million", "billion", "trillion")
_EN_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _three_en(n: int) -> str:
    """0–999 的英文读法。"""
    out: list[str] = []
    if n >= 100:
        out.append(_EN_ONES[n // 100] + " hundred")
        n %= 100
        if n:
            out.append("and")
    if n >= 20:
        tens, ones = divmod(n, 10)
        out.append(_EN_TENS[tens] + (("-" + _EN_ONES[ones]) if ones else ""))
    elif n:
        out.append(_EN_ONES[n])
    return " ".join(out)


def int_to_en(n: int) -> str:
    """整数转英文：2026 → two thousand twenty-six。"""
    n = int(n)
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + int_to_en(-n)

    groups: list[int] = []
    while n > 0:
        groups.append(n % 1000)
        n //= 1000

    parts: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        block = groups[index]
        if not block:
            continue
        text = _three_en(block)
        if index:
            text += " " + _EN_BIG[index]
        parts.append(text)
    return " ".join(parts)


_ORD_WORDS = {
    "one": "first", "two": "second", "three": "third", "five": "fifth",
    "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
}


def _ordinal_word(word: str) -> str:
    if word in _ORD_WORDS:
        return _ORD_WORDS[word]
    if word.endswith("y"):
        return word[:-1] + "ieth"
    return word + "th"


def _ordinal_en(n: int) -> str:
    """22 → twenty-second（连字符的只变最后一个词）。"""
    word = int_to_en(n)
    if "-" in word:
        head, last = word.rsplit("-", 1)
        return head + "-" + _ordinal_word(last)
    return _ordinal_word(word)


def digits_to_en(text: str) -> str:
    return " ".join(_EN_ONES[int(ch)] if ch.isdigit() else ch for ch in text)


def _read_year_en(token: str) -> str:
    """2026 → twenty twenty-six（英语里年份习惯两位两位读）。"""
    year = int(token)
    if 1100 <= year <= 2099:
        high, low = divmod(year, 100)
        if low == 0:
            return int_to_en(high) + " hundred"
        if low < 10:
            return int_to_en(high) + " oh " + _EN_ONES[low]
        return int_to_en(high) + " " + int_to_en(low)
    return int_to_en(year)


def _read_number_en(token: str) -> str:
    if "." in token:
        whole, frac = token.split(".", 1)
        head = int_to_en(int(whole)) if whole else "zero"
        return head + " point " + digits_to_en(frac)
    if len(token) >= LONG_DIGITS:
        return digits_to_en(token)
    return int_to_en(int(token))


# ==================== 语境判断 ====================


_DELIMS = "。！？；，,.!?;:\n"


def _context_is_english(match: "re.Match", window: int = 10) -> bool:
    """数字所在的那一小截是英文还是中文。

    只看**最近的标点**之间那一段：中英混排时，
    "Open 2 files，然后把 3 个删掉" 里的 3 不该被左边的英文带偏。
    """
    text = match.string or ""
    left = text[max(0, match.start() - window) : match.start()]
    right = text[match.end() : match.end() + window]
    for delim in _DELIMS:
        if delim in left:
            left = left.rsplit(delim, 1)[1]
        if delim in right:
            right = right.split(delim, 1)[0]
    ctx = left + right
    latin = sum(1 for ch in ctx if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in ctx if "\u4e00" <= ch <= "\u9fff")
    return latin > cjk


# ==================== 正则 ====================

_RE_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_RE_DATE_DASH = re.compile(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
_RE_DATE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_RE_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?(?!\d)")
_RE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RE_MONEY = re.compile(r"([¥￥$])\s*(\d+(?:\.\d+)?)(?:\s*(万|亿))?")
_RE_ORDINAL = re.compile(r"第\s*(\d+)")
_RE_YEAR = re.compile(r"(?<!\d)(\d{4})\s*年")
_RE_RANGE = re.compile(r"(?<=\d)\s*[-~～]\s*(?=\d)")
_RE_WAN = re.compile(r"(\d+(?:\.\d+)?)\s*(万|亿)")
_RE_DECIMAL = re.compile(r"(?<!\d)(\d+\.\d+)(?!\d)")
_RE_INT = re.compile(r"(?<!\d)(\d+)(?!\d)")


# ==================== 主流程 ====================


def normalize_numbers(text: str) -> str:
    """按语境把数字换成中文或英文读法。"""
    text = str(text or "")
    if not text:
        return text

    def _date(m: "re.Match") -> str:
        year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
        if _context_is_english(m):
            name = _EN_MONTHS[month] if 1 <= month <= 12 else str(month)
            return f"{name} {_ordinal_en(day)}, {_read_year_en(year)}"
        return f"{_read_year_zh(year)}年{int_to_zh(month)}月{int_to_zh(day)}日"

    text = _RE_DATE_CN.sub(_date, text)
    text = _RE_DATE_DASH.sub(_date, text)

    def _date_md(m: "re.Match") -> str:
        month, day = int(m.group(1)), int(m.group(2))
        if _context_is_english(m):
            name = _EN_MONTHS[month] if 1 <= month <= 12 else str(month)
            return f"{name} {_ordinal_en(day)}"
        return f"{int_to_zh(month)}月{int_to_zh(day)}日"

    text = _RE_DATE_MD.sub(_date_md, text)

    def _time(m: "re.Match") -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        second = int(m.group(3)) if m.group(3) else 0
        if _context_is_english(m):
            if minute == 0:
                out = int_to_en(hour) + " o'clock"
            elif minute < 10:
                out = f"{int_to_en(hour)} oh {_EN_ONES[minute]}"
            else:
                out = f"{int_to_en(hour)} {int_to_en(minute)}"
            if second:
                out += " and " + int_to_en(second) + " seconds"
            return out
        out = f"{int_to_zh(hour)}点"
        if minute:
            out += ("零" + DIGITS[minute] + "分") if minute < 10 else f"{int_to_zh(minute)}分"
        if second:
            out += f"{int_to_zh(second)}秒"
        return out

    text = _RE_TIME.sub(_time, text)

    def _percent(m: "re.Match") -> str:
        if _context_is_english(m):
            return _read_number_en(m.group(1)) + " percent"
        return "百分之" + _read_number_zh(m.group(1))

    text = _RE_PERCENT.sub(_percent, text)

    def _money(m: "re.Match") -> str:
        symbol, amount, unit = m.group(1), m.group(2), m.group(3) or ""
        if _context_is_english(m):
            currency = "dollars" if symbol == "$" else "yuan"
            amount_text = _read_number_en(amount)
            if unit:
                amount_text = int_to_en(int(float(unit) * float(amount))) + " " + currency
                return amount_text
            return amount_text + " " + currency
        return _read_number_zh(amount) + unit + "元"

    text = _RE_MONEY.sub(_money, text)

    def _ordinal(m: "re.Match") -> str:
        if _context_is_english(m):
            return "number " + _read_number_en(m.group(1))
        return "第" + _read_number_zh(m.group(1))

    text = _RE_ORDINAL.sub(_ordinal, text)

    def _year(m: "re.Match") -> str:
        if _context_is_english(m):
            return _read_year_en(m.group(1))
        return _read_year_zh(m.group(1)) + "年"

    text = _RE_YEAR.sub(_year, text)

    text = _RE_RANGE.sub(lambda m: " to " if _context_is_english(m) else "到", text)

    def _wan(m: "re.Match") -> str:
        value = float(m.group(1))
        unit = m.group(2)
        if _context_is_english(m):
            scale = 10000 if unit == "万" else 100000000
            return int_to_en(int(value * scale))
        return int_to_zh(int(value)) + unit

    text = _RE_WAN.sub(_wan, text)

    def _number(m: "re.Match") -> str:
        if _context_is_english(m):
            return _read_number_en(m.group(1))
        return _read_number_zh(m.group(1))

    text = _RE_DECIMAL.sub(_number, text)
    text = _RE_INT.sub(_number, text)
    return text
