"""把聊天文本里的网址变成可点击的超链接。

判断标准就两条：**带网络协议**（`https://` 等）或者**像域名**（`xxx.com`、`www.xxx.cn`）。
显示的文字一个字都不改，只是给它套一个 `<a>`——用户要看的是原样，点的是链接。

注意：Qt 的富文本会把连续空格压成一个，所以换行和空格要自己补回去，
否则气泡里的排版会和纯文本长得不一样。
"""

from __future__ import annotations

import html
import re

# 常见顶级域名。宁可少认几个，也别把 `report.xlsx`、`3.14` 当成网址。
_TLDS = (
    "com|cn|net|org|io|ai|dev|app|me|co|top|xyz|info|tv|cc|edu|gov|biz|site|"
    "online|tech|store|cloud|link|so|hk|tw|jp|kr|us|uk|de|fr|ru|au|ca|in|br|nl|se|no|es|it|ch|at|be|pl|sg|my|id|ph|vn|th"
)

# 协议开头 或 www 开头 或 裸域名
# 中文标点也算"网址到此为止"，否则 "www.x.com，然后" 会把逗号吃进去
_STOP = r"""[^\s<>"'，。；：！？、（）【】「」『』《》]+"""
_URL = re.compile(
    r"(?i)(?:"
    r"(?:https?|ftps?|wss?|sftp|ssh|smb|file|git)://" + _STOP
    + r"|www\." + _STOP
    + rf"|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{_TLDS})"
    + r"(?:/" + _STOP + r")?"
    + r")"
)

# 句末标点常常紧贴网址，链接里不该算
_TRAILING = ".,;:!?、，。；：！？）)】」』\"'"

# href 里没协议时补上，点击才打得开
_DEFAULT_SCHEME = "https://"

# 链接颜色跟着主题走（深色底上要够亮）
try:
    from . import theme as _theme

    _LINK_COLOR = getattr(_theme, "ACCENT", "#5CE1C8")
except Exception:  # 单独跑这个模块时（比如测试）没有主题
    _LINK_COLOR = "#5CE1C8"


def _clean_url(raw: str) -> str:
    """去掉紧贴在网址末尾的标点。"""
    url = raw
    while url and url[-1] in _TRAILING:
        url = url[:-1]
    return url


def href_for(url: str) -> str:
    """给网址补全协议，用于 a 标签的 href。"""
    low = url.lower()
    if low.startswith(("http://", "https://", "ftp://", "ftps://", "file://")):
        return url
    if low.startswith("www."):
        return _DEFAULT_SCHEME + url
    return _DEFAULT_SCHEME + url


def find_urls(text: str) -> list[str]:
    """按出现顺序返回文本里的网址（已去掉尾部标点）。"""
    found = []
    for match in _URL.finditer(str(text or "")):
        url = _clean_url(match.group(0))
        if url:
            found.append(url)
    return found


def has_url(text: str) -> bool:
    return bool(find_urls(text))


def _escape_block(text: str) -> str:
    """转义 + 保住换行和连续空格（富文本会吃掉它们）。"""
    out = html.escape(text, quote=False)
    out = out.replace("\n", "<br>")
    # 两个以上空格：前 n-1 个用不换行空格顶住
    return re.sub(r"  +", lambda m: "&nbsp;" * (len(m.group(0)) - 1) + " ", out)


def linkify(text: str, href_clickable: bool = True) -> str:
    """把纯文本转成带链接的 HTML。没有网址时返回转义后的普通内容。"""
    source = str(text or "")
    if not source:
        return ""

    pieces: list[str] = []
    last = 0
    for match in _URL.finditer(source):
        url = _clean_url(match.group(0))
        if not url:
            continue
        start = match.start()
        end = start + len(url)
        if start > last:
            pieces.append(_escape_block(source[last:start]))
        label = _escape_block(url)
        if href_clickable:
            pieces.append(
                f'<a href="{html.escape(href_for(url), quote=True)}" '
                f'style="color:{_LINK_COLOR}; text-decoration:underline;">{label}</a>'
            )
        else:
            pieces.append(label)
        last = end
    pieces.append(_escape_block(source[last:]))
    body = "".join(pieces)
    return f'<span style="white-space: pre-wrap;">{body}</span>'
