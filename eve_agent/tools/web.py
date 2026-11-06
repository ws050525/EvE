"""联网工具：搜索与抓网页。

为什么不做"打开浏览器点一点"：
- 点击 + 截图 + 视觉定位，一步 2~11 秒，还容易点错；
- 而"查资料总结"本质是**读文本**，HTTP 抓取又准又快。
所以给 agent 的是 web_search / http_get 两个工具，浏览器自动化只留给真正需要交互的场景。
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .registry import ToolRegistry, ToolSpec

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
MAX_BYTES = 2_000_000
DEFAULT_CHARS = 3000
MAX_CHARS = 8000

_BLOCK_TAGS = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_BREAK_TAGS = re.compile(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\x0b\f\r]+")
_BLANKS = re.compile(r"\n\s*\n+")


def _fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES)
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _to_text(markup: str) -> str:
    text = _BLOCK_TAGS.sub(" ", markup)
    text = _BREAK_TAGS.sub("\n", text)
    text = _ANY_TAG.sub(" ", text)
    text = html_lib.unescape(text)
    text = _SPACES.sub(" ", text)
    text = _BLANKS.sub("\n", text)
    return text.strip()


def web_search(args: dict[str, Any]) -> str:
    """走 Bing 的 RSS 输出——比抓 HTML 稳，也不用跟 DOM 结构较劲。"""
    query = str(args.get("query", "")).strip()
    if not query:
        return "[error] 缺少 query"
    try:
        limit = max(1, min(int(args.get("max_results", 5) or 5), 10))
    except (TypeError, ValueError):
        limit = 5

    url = "https://cn.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    try:
        raw = _fetch(url)
        root = ET.fromstring(raw)
    except Exception as exc:
        return f"[error] 搜索失败：{exc}"

    items = root.findall(".//item")[:limit]
    if not items:
        return "[error] 没有搜索结果"

    lines: list[str] = []
    for index, item in enumerate(items, 1):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = _to_text(item.findtext("description") or "")[:180]
        if not link:
            continue
        lines.append(f"{index}. {title[:90]}\n   {link}\n   {desc}")

    if not lines:
        return "[error] 结果解析为空"
    return "[ok] 搜索结果：\n" + "\n".join(lines)


def http_get(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "[error] 需要完整的 http(s) 地址"
    try:
        limit = max(200, min(int(args.get("max_chars", DEFAULT_CHARS) or DEFAULT_CHARS), MAX_CHARS))
    except (TypeError, ValueError):
        limit = DEFAULT_CHARS

    try:
        page = _fetch(url)
    except Exception as exc:
        return f"[error] 抓取失败：{exc}"

    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    title = _to_text(title_match.group(1))[:120] if title_match else ""

    # 优先取正文容器：菜鸟/文档站这类页面，导航栏占的篇幅比正文还大
    body = page
    for pattern in (r"<article[^>]*>(.*?)</article>", r"<main[^>]*>(.*?)</main>"):
        found = re.search(pattern, page, re.S | re.I)
        if found and len(_to_text(found.group(1))) > 400:
            body = found.group(1)
            break

    text = _to_text(body)
    if not text:
        return "[error] 页面没有可读正文（可能是纯 JS 渲染）"

    truncated = len(text) > limit
    head = f"[ok] {url}\n标题：{title}\n正文：\n" if title else f"[ok] {url}\n正文：\n"
    tail = f"\n…（截断，全文 {len(text)} 字符）" if truncated else ""
    return head + text[:limit] + tail


def register_web(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="web_search",
            description=(
                "用 Bing 搜索，返回标题、链接和摘要。"
                "query 要用简短关键词（例如 'asyncio python'），不要用整句问句——"
                "整句会让搜索结果变差。找到线索后再用 http_get 打开页面读正文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词"},
                    "max_results": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
            handler=web_search,
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="http_get",
            description=(
                "抓取一个网页并抽取纯文本（自动去掉标签）。用于阅读搜索结果里的具体页面。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 http(s) 地址"},
                    "max_chars": {
                        "type": "integer",
                        "description": f"最多返回多少字符，默认 {DEFAULT_CHARS}，上限 {MAX_CHARS}",
                    },
                },
                "required": ["url"],
            },
            handler=http_get,
            risk="low",
        )
    )
