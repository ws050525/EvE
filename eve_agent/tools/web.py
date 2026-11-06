"""联网工具：搜索与抓网页。

为什么不做"打开浏览器点一点"：
- 点击 + 截图 + 视觉定位，一步 2~11 秒，还容易点错；
- 而"查资料总结"本质是**读文本**，HTTP 抓取又准又快。
所以给 agent 的是 web_search / http_get 两个工具，浏览器自动化只留给真正需要交互的场景。
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec

DEEPSEEK_SEARCH = "https://api.deepseek.com/v1/tools/search"
DEEPSEEK_FETCH = "https://api.deepseek.com/v1/tools/fetch"


@dataclass
class WebContext:
    """后端选择：有 DeepSeek key 就用官方搜索，否则退回 Bing 抓取。"""

    data_dir: Path | None = None
    backend: str = "auto"  # auto | deepseek | bing


def _deepseek_key(ctx: WebContext) -> str:
    if ctx.backend == "bing":
        return ""
    env = os.environ.get("EVE_CLOUD_API_KEY", "").strip()
    if env:
        return env
    if ctx.data_dir is not None:
        try:
            from eve_core.secrets import KeyStore

            return KeyStore(ctx.data_dir).get("deepseek") or ""
        except Exception:
            return ""
    return ""


def _post_json(url: str, payload: dict, key: str, timeout: float = 40.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

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


def web_search(args: dict[str, Any], ctx: WebContext | None = None) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "[error] 缺少 query"
    try:
        limit = max(1, min(int(args.get("max_results", 5) or 5), 10))
    except (TypeError, ValueError):
        limit = 5

    ctx = ctx or WebContext()
    key = _deepseek_key(ctx)
    if key:
        try:
            return _search_deepseek(query, limit, key)
        except Exception as exc:
            # 云端搜索失败就退回免费方案，不让工具直接失败
            fallback = _search_bing(query, limit)
            return f"[warn] DeepSeek 搜索失败（{exc}），已回退 Bing\n{fallback}"
    return _search_bing(query, limit)


def _search_deepseek(query: str, limit: int, key: str) -> str:
    data = _post_json(
        DEEPSEEK_SEARCH,
        {"query": query, "count": limit, "need_content": False},
        key,
    )
    results = data.get("results") or []
    if not results:
        return "[error] 没有搜索结果"
    lines: list[str] = []
    for index, item in enumerate(results, 1):
        title = str(item.get("title") or "")[:90]
        url = str(item.get("url") or "")
        summary = str(item.get("summary") or item.get("snippet") or "").strip()
        if not url:
            continue
        lines.append(f"{index}. {title}\n   {url}\n   {summary[:320]}")
    return "[ok] 搜索结果（DeepSeek）：\n" + "\n".join(lines)


def _search_bing(query: str, limit: int) -> str:
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


def http_get(args: dict[str, Any], ctx: WebContext | None = None) -> str:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "[error] 需要完整的 http(s) 地址"
    try:
        limit = max(200, min(int(args.get("max_chars", DEFAULT_CHARS) or DEFAULT_CHARS), MAX_CHARS))
    except (TypeError, ValueError):
        limit = DEFAULT_CHARS

    ctx = ctx or WebContext()
    key = _deepseek_key(ctx)
    if key:
        try:
            data = _post_json(DEEPSEEK_FETCH, {"url": url}, key)
            title = str(data.get("title") or "")[:120]
            content = str(data.get("content") or "")
            if content:
                head = f"[ok] {url}\n标题：{title}\n正文：\n" if title else f"[ok] {url}\n正文：\n"
                truncated = len(content) > limit
                tail = f"\n…（截断，全文 {len(content)} 字符）" if truncated else ""
                return head + content[:limit] + tail
        except Exception:
            pass  # 云端抓取失败就走本地抽取

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


def register_web(registry: ToolRegistry, cfg=None) -> None:
    ctx = WebContext(
        data_dir=Path(cfg.data_dir) if cfg is not None else None,
        backend=getattr(cfg, "web_backend", "auto") if cfg is not None else "auto",
    )
    has_key = bool(_deepseek_key(ctx))
    backend_hint = "DeepSeek 官方搜索" if has_key else "Bing 抓取（未配置 DeepSeek key）"

    registry.register(
        ToolSpec(
            name="web_search",
            description=(
                f"搜索网页，返回标题、链接和摘要。当前后端：{backend_hint}。"
                "query 用简短关键词（例如 'asyncio python'），不要用整句问句。"
                "找到线索后再用 http_get 打开页面读正文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词"},
                    "max_results": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
            handler=lambda args: web_search(args, ctx),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="http_get",
            description=(
                "抓取一个网页并抽取正文。用于阅读搜索结果里的具体页面。"
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
            handler=lambda args: http_get(args, ctx),
            risk="low",
        )
    )
