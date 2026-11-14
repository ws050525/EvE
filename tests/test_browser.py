"""浏览器工具的测试：参数校验、工具注册、失败提示。

真正驱动浏览器的部分要开进程，放到 tests 之外手动跑（见 docs/MEMORY.md 的实测记录），
这里只测不需要浏览器的逻辑，保证 CI/日常跑测试不会卡在启动浏览器上。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.tools.browser import (  # noqa: E402
    BrowserSettings,
    _click,
    _close,
    _open,
    _read,
    _type,
    _json_literal,
    find_browser,
    register_browser,
)
from eve_agent.tools.registry import ToolRegistry  # noqa: E402

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


class FakeSession:
    """冒充 BrowserSession：不启浏览器，只按剧本回话。"""

    def __init__(self, running=True):
        self.running = running
        self.calls = []

    def open(self, url, show=None):
        self.calls.append(("open", url, show))
        return {"url": url, "title": "示例页", "text": "第一段\n第二段"}

    def text(self, limit):
        self.calls.append(("text", limit))
        return "页面正文" * 20

    def info(self):
        return {"url": "https://example.com/a", "title": "示例页", "text": ""}

    def click(self, target):
        self.calls.append(("click", target))
        return "text:" + target

    def type(self, target, text, submit=False):
        self.calls.append(("type", target, text, submit))
        return "INPUT:" + target

    def candidates(self):
        return ["登录", "注册", "下一页"]

    def screenshot(self, name=""):
        return Path(r"D:\1\EVE\.tmp\browser\fake.png")

    def back(self):
        return True

    def close(self):
        self.calls.append(("close",))


@case
def json_literal_escapes_quotes():
    import json

    raw = 'he said "hi"\n换行'
    assert json.loads(_json_literal(raw)) == raw


@case
def open_rejects_non_http():
    session = FakeSession()
    out = _open({"url": "file:///C:/secret.txt"}, session)
    assert out.startswith("[error]") and "http" in out, out
    assert not session.calls, "非法地址不该真的去开"


@case
def read_before_open_is_a_clear_error():
    out = _read({}, FakeSession(running=False))
    assert out.startswith("[error]") and "browser_open" in out, out


@case
def click_reports_what_is_clickable():
    class MissSession(FakeSession):
        def click(self, target):
            return ""

    out = _click({"target": "不存在的按钮"}, MissSession())
    assert out.startswith("[error]"), out
    assert "登录" in out and "下一页" in out, out


@case
def click_success_mentions_where_we_are():
    out = _click({"target": "登录"}, FakeSession())
    assert out.startswith("[ok]") and "示例页" in out, out


@case
def type_passes_submit_flag():
    session = FakeSession()
    out = _type({"target": "搜索", "text": "关键词", "submit": True}, session)
    assert out.startswith("[ok]"), out
    assert ("type", "搜索", "关键词", True) in session.calls, session.calls


@case
def close_is_idempotent():
    session = FakeSession(running=False)
    out = _close({}, session)
    assert out.startswith("[ok]") and "本来就没开" in out, out


@case
def find_browser_returns_a_real_path_or_nothing():
    found = find_browser()
    assert isinstance(found, str)
    if found:
        assert Path(found).exists(), found


@case
def browser_tools_are_registered():
    registry = ToolRegistry()
    register_browser(registry, None)
    names = set(registry.names())
    expected = {
        "browser_open",
        "browser_read",
        "browser_click",
        "browser_type",
        "browser_wait",
        "browser_screenshot",
        "browser_back",
        "browser_close",
    }
    assert expected <= names, expected - names


@case
def browser_settings_have_sane_defaults():
    settings = BrowserSettings()
    assert settings.headless is True
    assert settings.port > 1024, settings.port


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
