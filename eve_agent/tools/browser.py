"""浏览器真交互：驱动本机已装的 Edge（走 CDP，不下载任何浏览器）。

为什么不按 `http_get` 的老路走：抓正文只能读，遇到"点开详情 / 登录后才有内容 /
需要翻页"就废了。而装一套 Playwright 要额外拉 100MB+ 的 Chromium，
这台机器上已经有 Edge，用 CDP 直接连它最省事——不需要驱动、不需要下载。

通信方式：启动 Edge 时带 `--remote-debugging-port`，然后通过 WebSocket 发 CDP 命令。
点击/输入都走 JS 注入（`element.click()`、原生 value setter + input 事件），
回车提交用 CDP 的 `Input.dispatchKeyEvent`——纯 JS 派的 KeyboardEvent 不触发默认提交。

默认无头（快、不打扰）。需要人工登录/过验证码时，`browser_open(url, show=true)`
会以**有头**模式启动，第一次调用决定这一次会话的形态。
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import ToolRegistry, ToolSpec

DEFAULT_PORT = 9333
NAV_TIMEOUT = 25.0
CMD_TIMEOUT = 30.0
MAX_TEXT = 12_000
DEFAULT_TEXT = 4_000

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def find_browser() -> str:
    for path in _EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _json_literal(value: str) -> str:
    """把 Python 字符串变成安全的 JS 字面量。"""
    return json.dumps(str(value), ensure_ascii=False)


@dataclass
class BrowserSettings:
    headless: bool = True
    port: int = DEFAULT_PORT
    binary: str = ""
    profile: str = ""


class BrowserSession:
    """一个进程内只开一个浏览器。所有工具调用共用这个会话。"""

    def __init__(self, settings: BrowserSettings, work_root: Path):
        self.settings = settings
        self.work_root = Path(work_root)
        self._proc: subprocess.Popen | None = None
        self._ws = None
        self._msg_id = 0
        self._lock = threading.Lock()
        self._headless = settings.headless
        self.last_error = ""

    # ---------- 生命周期 ----------

    @property
    def running(self) -> bool:
        return self._ws is not None and self._proc is not None and self._proc.poll() is None

    def ensure(self, show: bool | None = None) -> None:
        if self.running:
            return
        binary = self.settings.binary or find_browser()
        if not binary:
            raise RuntimeError(
                "没找到 Edge/Chrome。装一个，或者在 config.toml 的 [browser] binary 里写全路径。"
            )

        if show is not None:
            self._headless = not show

        profile = Path(self.settings.profile) if self.settings.profile else (
            self.work_root / ".tmp" / "browser-profile"
        )
        profile.mkdir(parents=True, exist_ok=True)

        args = [
            binary,
            f"--remote-debugging-port={self.settings.port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1280,900",
        ]
        if self._headless:
            args.append("--headless=new")
        args.append("about:blank")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags if self._headless else 0,
        )

        targets = self._wait_for_targets()
        page = next((item for item in targets if item.get("type") == "page"), None)
        if page is None:
            raise RuntimeError("浏览器起来了但没有可用的页面目标")

        import websocket  # websocket-client

        self._ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=CMD_TIMEOUT)
        self._call("Page.enable")
        self._call("Runtime.enable")

    def _wait_for_targets(self) -> list[dict]:
        deadline = time.time() + NAV_TIMEOUT
        url = f"http://127.0.0.1:{self.settings.port}/json/list"
        last = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, list) and data:
                    return data
            except Exception as exc:  # 端口还没起来是常态，继续等
                last = str(exc)
            time.sleep(0.2)
        raise RuntimeError(f"连不上浏览器调试端口 {self.settings.port}：{last}")

    def close(self) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # ---------- CDP ----------

    def _call(self, method: str, params: dict | None = None) -> dict:
        """发一条 CDP 命令并等到对应 id 的回复。"""
        if self._ws is None:
            raise RuntimeError("浏览器没有启动")
        with self._lock:
            self._msg_id += 1
            message_id = self._msg_id
            self._ws.send(
                json.dumps({"id": message_id, "method": method, "params": params or {}})
            )
            deadline = time.time() + CMD_TIMEOUT
            while time.time() < deadline:
                raw = self._ws.recv()
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == message_id:
                    if "error" in message:
                        raise RuntimeError(str(message["error"].get("message") or message["error"]))
                    return message.get("result") or {}
            raise RuntimeError(f"CDP 命令超时：{method}")

    def _eval(self, expression: str, retries: int = 8) -> Any:
        """执行 JS 取值。导航过程中上下文会被销毁，这种错误重试即可。"""
        transient = (
            "Inspected target navigated",
            "Cannot find context",
            "Execution context was destroyed",
            "Not attached to an active page",
            "Target closed",
        )
        last = ""
        for _ in range(max(1, retries)):
            try:
                result = self._call(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True, "awaitPromise": True},
                )
                payload = result.get("result") or {}
                if payload.get("subtype") == "error":
                    raise RuntimeError(str(payload.get("description") or "JS 执行失败"))
                return payload.get("value")
            except RuntimeError as exc:
                last = str(exc)
                if not any(key in last for key in transient):
                    raise
                time.sleep(0.3)
        raise RuntimeError(last)

    def _wait_ready(self, timeout: float = NAV_TIMEOUT) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self._eval("document.readyState")
            except Exception:
                time.sleep(0.15)
                continue
            if state in ("interactive", "complete"):
                time.sleep(0.4)  # 让首屏 JS 渲染完
                return
            time.sleep(0.15)

    # ---------- 动作 ----------

    def open(self, url: str, show: bool | None = None) -> dict:
        if not url.startswith(("http://", "https://")):
            raise ValueError("只支持 http/https 地址")
        self.ensure(show=show)
        self._call("Page.navigate", {"url": url})
        self._wait_ready()
        return self.info()

    def info(self) -> dict:
        data = self._eval(
            "({url: location.href, title: document.title, "
            "text: (document.body ? document.body.innerText : '').slice(0, 200)})"
        )
        return data or {}

    def text(self, max_chars: int = DEFAULT_TEXT) -> str:
        max_chars = max(200, min(MAX_TEXT, int(max_chars)))
        value = self._eval("(document.body ? document.body.innerText : '') || ''")
        text = str(value or "").strip()
        if not text:
            # 刚跳过来还在渲染：等一会儿再看一次
            self._wait_ready(timeout=8)
            value = self._eval("(document.body ? document.body.innerText : '') || ''")
            text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n…（截断，全文 {len(text)} 字符）"

    def click(self, target: str) -> str:
        script = f"""
        (function() {{
          const raw = {_json_literal(target)};
          const visible = (el) => {{
            const box = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return box.width > 2 && box.height > 2 && style.visibility !== 'hidden'
                   && style.display !== 'none' && style.opacity !== '0';
          }};
          const label = (el) => ((el.innerText || el.value || el.getAttribute('aria-label')
                                 || el.getAttribute('title') || el.tagName) + '').trim().slice(0, 60);
          try {{
            const bySelector = document.querySelector(raw);
            if (bySelector) {{
              bySelector.scrollIntoView({{block: 'center'}});
              bySelector.click();
              return 'selector:' + (label(bySelector) || raw);
            }}
          }} catch (e) {{}}
          const nodes = Array.from(document.querySelectorAll(
            'a,button,input,textarea,select,[role="button"],[onclick],label,summary,span,div,li'));
          const visibleNodes = nodes.filter(visible);
          const exact = visibleNodes.find((el) => label(el) === raw);
          const contains = visibleNodes.find((el) => label(el).includes(raw));
          const pick = exact || contains;
          if (!pick) return '';
          pick.scrollIntoView({{block: 'center'}});
          pick.click();
          return 'text:' + label(pick);
        }})()
        """
        hit = self._eval(script)
        if not hit:
            return ""
        # 点了之后可能是跳转，也可能只是页面内变化：跳转就等加载完
        before = ""
        try:
            before = str(self._eval("location.href") or "")
        except Exception:
            pass
        time.sleep(0.5)
        try:
            after = str(self._eval("location.href") or "")
        except Exception:
            after = ""
        if after and after != before:
            self._wait_ready()
        return str(hit)

    def type(self, target: str, text: str, submit: bool = False) -> str:
        script = f"""
        (function() {{
          const raw = {_json_literal(target)};
          const value = {_json_literal(text)};
          let el = null;
          try {{ el = document.querySelector(raw); }} catch (e) {{ el = null; }}
          if (!el) {{
            const nodes = Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]'));
            el = nodes.find((node) => {{
              const hint = (node.placeholder || '') + (node.name || '') + (node.id || '')
                         + (node.getAttribute('aria-label') || '');
              return hint.includes(raw);
            }}) || null;
          }}
          if (!el) return '';
          el.focus();
          const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value');
          if (setter && setter.set) setter.set.call(el, value);
          else el.value = value;
          el.dispatchEvent(new Event('input', {{bubbles: true}}));
          el.dispatchEvent(new Event('change', {{bubbles: true}}));
          return (el.tagName + ':' + (el.name || el.id || el.placeholder || raw)).slice(0, 60);
        }})()
        """
        hit = self._eval(script)
        if not hit:
            return ""
        if submit:
            self._press_enter()
            time.sleep(0.8)
        return str(hit)

    def _press_enter(self) -> None:
        base = {
            "key": "Enter",
            "code": "Enter",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
        }
        self._call("Input.dispatchKeyEvent", {"type": "keyDown", **base})
        self._call("Input.dispatchKeyEvent", {"type": "char", "text": "\r", **base})
        self._call("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    def wait_for(self, selector: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + max(1.0, min(30.0, timeout))
        expression = f"!!document.querySelector({_json_literal(selector)})"
        while time.time() < deadline:
            try:
                if self._eval(expression):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def screenshot(self, name: str = "") -> Path:
        data = self._call("Page.captureScreenshot", {"format": "png"})["data"]
        folder = self.work_root / ".tmp" / "browser"
        folder.mkdir(parents=True, exist_ok=True)
        filename = name or f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        path = folder / filename
        path.write_bytes(base64.b64decode(data))
        return path

    def back(self) -> bool:
        """后退一页。已经在最早一页时返回 False，不报错。"""
        try:
            length = int(self._eval("history.length") or 1)
        except Exception:
            length = 1
        if length <= 1:
            return False
        self._eval("history.back()")
        self._wait_ready()
        return True

    def candidates(self, limit: int = 12) -> list[str]:
        """页面上现在能点的文字，点击失败时回给模型当提示。"""
        script = f"""
        (function() {{
          const out = [];
          const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],input[type="submit"]'));
          for (const el of nodes) {{
            const box = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            if (box.width < 3 || box.height < 3) continue;
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            const text = ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '')
              .trim().replace(/\\s+/g, ' ');
            if (text && text.length <= 30 && !out.includes(text)) out.push(text);
            if (out.length >= {int(limit)}) break;
          }}
          return out;
        }})()
        """
        try:
            value = self._eval(script)
        except Exception:
            return []
        return [str(item) for item in value or []]


_SESSION: BrowserSession | None = None


def session_for(settings: BrowserSettings, work_root: Path) -> BrowserSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = BrowserSession(settings, work_root)
        atexit.register(_SESSION.close)
    else:
        _SESSION.settings = settings
        _SESSION.work_root = Path(work_root)
    return _SESSION


# ---------- 工具层 ----------


def _open(args: dict[str, Any], session: BrowserSession) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return "[error] 缺少 url"
    if not url.startswith(("http://", "https://")):
        return "[error] 只支持 http/https 地址"
    show = args.get("show")
    try:
        info = session.open(url, show=True if show else (False if show is not None else None))
    except Exception as exc:
        return f"[error] 打开失败：{type(exc).__name__}: {exc}"

    title = str(info.get("title") or "").strip()
    head = str(info.get("text") or "").strip().replace("\n", " ")[:120]
    return f"[ok] 已打开 {info.get('url') or url}\n标题：{title}\n首屏：{head}"


def _read(args: dict[str, Any], session: BrowserSession) -> str:
    if not session.running:
        return "[error] 浏览器还没打开，先调 browser_open"
    try:
        limit = int(args.get("max_chars") or DEFAULT_TEXT)
    except (TypeError, ValueError):
        limit = DEFAULT_TEXT
    try:
        text = session.text(limit)
    except Exception as exc:
        return f"[error] 读取失败：{type(exc).__name__}: {exc}"
    if not text:
        return "[error] 页面没有可读文本（可能是纯画布应用）"
    info = session.info()
    return f"[ok] {info.get('url')}\n标题：{info.get('title')}\n正文：\n{text}"


def _click(args: dict[str, Any], session: BrowserSession) -> str:
    if not session.running:
        return "[error] 浏览器还没打开，先调 browser_open"
    target = str(args.get("target") or "").strip()
    if not target:
        return "[error] 缺少 target（CSS 选择器或可见文字）"
    try:
        hit = session.click(target)
    except Exception as exc:
        return f"[error] 点击失败：{type(exc).__name__}: {exc}"
    if not hit:
        options = session.candidates()
        hint = ("；页面上的可点项：" + " / ".join(options[:10])) if options else ""
        return f"[error] 页面上没找到可点的“{target}”{hint}"
    info = session.info()
    return f"[ok] 点了 {hit}\n现在：{info.get('title')} · {info.get('url')}"


def _type(args: dict[str, Any], session: BrowserSession) -> str:
    if not session.running:
        return "[error] 浏览器还没打开，先调 browser_open"
    target = str(args.get("target") or "").strip()
    text = str(args.get("text") or "")
    if not target:
        return "[error] 缺少 target"
    try:
        hit = session.type(target, text, submit=bool(args.get("submit")))
    except Exception as exc:
        return f"[error] 输入失败：{type(exc).__name__}: {exc}"
    if not hit:
        return f"[error] 找不到输入框“{target}”（可以 browser_read 看看占位文字）"
    info = session.info()
    return f"[ok] 已输入到 {hit}{'，并提交' if args.get('submit') else ''}\n现在：{info.get('title')}"


def _wait(args: dict[str, Any], session: BrowserSession) -> str:
    if not session.running:
        return "[error] 浏览器还没打开，先调 browser_open"
    selector = str(args.get("selector") or "").strip()
    if not selector:
        return "[error] 缺少 selector"
    try:
        timeout = float(args.get("timeout") or 10)
    except (TypeError, ValueError):
        timeout = 10.0
    try:
        ok = session.wait_for(selector, timeout)
    except Exception as exc:
        return f"[error] 等待失败：{type(exc).__name__}: {exc}"
    return "[ok] 元素已出现" if ok else f"[error] {timeout:.0f}s 内没等到 {selector}"


def _screenshot(args: dict[str, Any], session: BrowserSession, root: Path) -> str:
    if not session.running:
        return "[error] 浏览器还没打开，先调 browser_open"
    try:
        path = session.screenshot(str(args.get("name") or ""))
    except Exception as exc:
        return f"[error] 截图失败：{type(exc).__name__}: {exc}"
    return f"[ok] 截图已保存：{path.relative_to(root) if path.is_relative_to(root) else path}"


def _back(args: dict[str, Any], session: BrowserSession) -> str:
    if not session.running:
        return "[error] 浏览器还没打开，先调 browser_open"
    moved = session.back()
    if not moved:
        return "[ok] 已经是最早的那一页，没得退了"
    info = session.info()
    return f"[ok] 已后退到 {info.get('title')} · {info.get('url')}"


def _close(args: dict[str, Any], session: BrowserSession) -> str:
    was = session.running
    session.close()
    return "[ok] 浏览器已关闭" if was else "[ok] 浏览器本来就没开"


def register_browser(registry: ToolRegistry, cfg=None) -> None:
    settings = BrowserSettings(
        headless=bool(getattr(cfg, "browser_headless", True)) if cfg else True,
        port=int(getattr(cfg, "browser_port", DEFAULT_PORT)) if cfg else DEFAULT_PORT,
        binary=str(getattr(cfg, "browser_binary", "") or "") if cfg else "",
        profile=str(getattr(cfg, "browser_profile", "") or "") if cfg else "",
    )
    from .workspace import _root_of

    root = _root_of(cfg)
    session = session_for(settings, root)
    binary = settings.binary or find_browser()
    hint = Path(binary).name if binary else "未找到浏览器"

    registry.register(
        ToolSpec(
            name="browser_open",
            description=(
                f"打开网页并真交互（用本机浏览器，当前：{hint}）。"
                "需要登录、点按钮、翻页、填写表单时用它；只是读正文用 http_get 更快。"
                "show=true 会以有头模式启动，方便人工登录/过验证码（只影响本次会话）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 http(s) 地址"},
                    "show": {
                        "type": "boolean",
                        "description": "true = 显示浏览器窗口（需要人工介入时）",
                    },
                },
                "required": ["url"],
            },
            handler=lambda args: _open(args, session),
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_read",
            description="读当前浏览器页面的可见正文（含导航、列表文字）。",
            parameters={
                "type": "object",
                "properties": {
                    "max_chars": {
                        "type": "integer",
                        "description": f"最多返回多少字符，默认 {DEFAULT_TEXT}，上限 {MAX_TEXT}",
                    }
                },
            },
            handler=lambda args: _read(args, session),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_click",
            description=(
                "点击页面元素。target 可以是 CSS 选择器（如 a.more），"
                "也可以是看得见的文字（如“下一页”“登录”）。找不到会返回错误，先 browser_read 看实际文字。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "CSS 选择器或可见文字"}
                },
                "required": ["target"],
            },
            handler=lambda args: _click(args, session),
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_type",
            description=(
                "往输入框里填内容。target 是选择器或占位文字/name/id 片段；"
                "submit=true 会补一个回车提交。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "选择器或输入框提示文字"},
                    "text": {"type": "string", "description": "要填的内容"},
                    "submit": {"type": "boolean", "description": "填完是否回车提交"},
                },
                "required": ["target", "text"],
            },
            handler=lambda args: _type(args, session),
            risk="medium",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_wait",
            description="等某个元素出现（单页应用加载慢时用），最多 30 秒。",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器"},
                    "timeout": {"type": "number", "description": "最多等几秒，默认 10"},
                },
                "required": ["selector"],
            },
            handler=lambda args: _wait(args, session),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_screenshot",
            description="截当前页面的图，存成 png 并返回路径（给用户看，或者留证据）。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "文件名，可省"}},
            },
            handler=lambda args: _screenshot(args, session, root),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_back",
            description="浏览器后退一页。",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _back(args, session),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="browser_close",
            description="关掉浏览器（任务结束、不再需要交互时调用，释放内存）。",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _close(args, session),
            risk="low",
        )
    )
