"""网址识别的测试：什么时候算网址、链接怎么生成、显示文字不能变。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_ui.links import find_urls, has_url, href_for, linkify

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def protocol_urls_are_found():
    assert find_urls("打开 https://www.baidu.com 看看") == ["https://www.baidu.com"]
    assert find_urls("http://a.io/x 和 ftp://b.cn/y") == ["http://a.io/x", "ftp://b.cn/y"]


@case
def bare_domains_are_found():
    assert find_urls("163.com 也行") == ["163.com"]
    assert find_urls("参考 www.example.com/a?b=1，然后") == ["www.example.com/a?b=1"]


@case
def non_urls_are_ignored():
    """文件名、小数、单个字母不该被当成网址。"""
    assert find_urls("report.xlsx 和 3.14 和 a.b") == []
    assert find_urls("版本 v1.2.3 已经发布") == []
    assert not has_url("没有任何链接")


@case
def chinese_punctuation_ends_the_url():
    urls = find_urls("看 www.abc.com，再看好 163.com。")
    assert urls == ["www.abc.com", "163.com"], urls


@case
def href_gets_a_scheme():
    assert href_for("https://a.com") == "https://a.com"
    assert href_for("www.a.com") == "https://www.a.com"
    assert href_for("a.com/x") == "https://a.com/x"


@case
def display_text_is_never_rewritten():
    """链接里的文字必须和原文一模一样：显示是显示，播报是播报。"""
    source = "打开 https://www.baidu.com 就行"
    html = linkify(source)
    assert "https://www.baidu.com" in html
    assert "三w" not in html, "播报的改写不能跑到显示里"
    assert "baidu.com" in html


@case
def html_is_escaped():
    html = linkify("有 <tag> 和 & 符号")
    assert "&lt;tag&gt;" in html
    assert "&amp;" in html


@case
def newlines_and_spaces_survive():
    html = linkify("第一行\n第二行  三个空格")
    assert "<br>" in html
    assert "&nbsp;" in html  # 富文本会吃掉连续空格


@case
def no_url_still_returns_escaped_text():
    assert linkify("") == ""
    assert "&lt;" in linkify("<x>")


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
    total = len(CHECKS)
    print(f"\n{total - failed}/{total} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
