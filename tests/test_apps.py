"""开始菜单索引与启动的单元测试。

不真的启动程序：launch_entry / 窗口探测全部替换成桩。
跑法： & 'D:\\python\\python.exe' tests\\test_apps.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.tools import apps, builtin

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


def entry(name: str, app_id: str = "", source: str = "start_menu") -> apps.AppEntry:
    return apps.AppEntry(name=name, app_id=app_id or f"D:/fake/{name}.exe", source=source)


class Fixture:
    """临时装一个假索引，并挡住所有真实副作用。"""

    def __init__(self, entries: list[apps.AppEntry]):
        self.entries = entries
        self.launched: list[apps.AppEntry] = []
        self.saved = {}

    def __enter__(self):
        index = apps.AppIndex()
        index._entries = list(self.entries)
        index._loaded_at = time.monotonic()
        index.source = "测试夹具"

        self.saved["index"] = apps._default_index
        self.saved["launch"] = apps.launch_entry
        self.saved["enum"] = builtin._enum_windows
        self.saved["wait"] = builtin._wait_new_window
        self.saved["exec"] = builtin._launch_executable
        self.saved["running"] = builtin._already_running

        apps._default_index = index
        apps.launch_entry = lambda e: self.launched.append(e) or e.name
        builtin._enum_windows = lambda: []
        builtin._wait_new_window = lambda before, timeout=0: None
        builtin._already_running = lambda exe: None
        builtin._launch_executable = lambda cmd, resolved=None: f"[stub] exec {cmd}"
        return self

    def __exit__(self, *exc):
        apps._default_index = self.saved["index"]
        apps.launch_entry = self.saved["launch"]
        builtin._enum_windows = self.saved["enum"]
        builtin._wait_new_window = self.saved["wait"]
        builtin._launch_executable = self.saved["exec"]
        builtin._already_running = self.saved["running"]
        return False


# ---------- 纯函数 ----------


@case
def parse_handles_array_and_single_object():
    many = '[{"Name":"微信","AppID":"com.tencent.wechat"},{"Name":"记事本","AppID":"D:\\\\n.exe"}]'
    parsed = apps.parse_start_apps(many)
    assert len(parsed) == 2, parsed
    assert parsed[0].name == "微信"

    # 只有一个条目时 ConvertTo-Json 返回对象而不是数组
    one = apps.parse_start_apps('{"Name":"计算器","AppID":"calc"}')
    assert len(one) == 1 and one[0].app_id == "calc", one


@case
def parse_survives_garbage():
    for bad in ("", "   ", "null", "{", "[]", '[{"Name":"x"}]', '[null]'):
        apps.parse_start_apps(bad)  # 不抛异常即可
    assert apps.parse_start_apps("[]") == []


@case
def rank_prefers_exact_then_prefix_then_contains():
    entries = [
        entry("企业微信"),        # 包含
        entry("微信开发者工具"),  # 前缀
        entry("微信"),            # 完全相同
        entry("微信输入法"),      # 前缀
    ]
    ranked = apps.rank(entries, "微信")
    assert [e.name for e in ranked] == [
        "微信",
        "微信输入法",
        "微信开发者工具",
        "企业微信",
    ], [e.name for e in ranked]


@case
def rank_shorter_name_wins_on_same_score():
    ranked = apps.rank([entry("企业微信"), entry("微信")], "微信")
    assert ranked[0].name == "微信", ranked


@case
def rank_matches_query_containing_name():
    ranked = apps.rank([entry("微信")], "打开微信电脑版")
    assert ranked and ranked[0].name == "微信", ranked


@case
def rank_can_fall_back_to_executable_name():
    ranked = apps.rank([entry("截图工具", "D:/tools/SnippingTool.exe")], "snippingtool")
    assert ranked and ranked[0].name == "截图工具", ranked


@case
def rank_ignores_unrelated():
    assert apps.rank([entry("微信"), entry("QQ")], "钉钉") == []


@case
def scan_shortcuts_uses_file_stem():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "微信.lnk").write_bytes(b"fake")
        (base / "sub").mkdir()
        (base / "sub" / "计算器.lnk").write_bytes(b"fake")
        (base / "readme.txt").write_bytes(b"x")
        found = apps.scan_shortcuts([base])
        names = sorted(e.name for e in found)
        assert names == ["微信", "计算器"], names
        assert all(e.source == "shortcut" for e in found)


# ---------- 索引 ----------


@case
def find_returns_exact_match_even_if_noisy():
    with Fixture([entry("微信"), entry("微信卸载")]) as fx:
        found = apps.default_index().find("微信")
        assert [e.name for e in found] == ["微信"], found
        assert fx.launched == []


@case
def find_filters_uninstallers_and_help_files():
    noisy = [
        entry("微信"),
        entry("微信卸载", "D:/x/uninst.exe"),
        entry("微信帮助", "D:/x/help.chm"),
    ]
    with Fixture(noisy):
        names = [e.name for e in apps.default_index().find("微信")]
        assert names == ["微信"], names


@case
def search_without_query_lists_clean_names():
    with Fixture([entry("微信"), entry("微信卸载"), entry("QQ")]):
        names = [e.name for e in apps.default_index().search("", 10)]
        assert names == ["QQ", "微信"], names


# ---------- app_launch ----------


@case
def launch_goes_through_start_menu():
    with Fixture([entry("微信", "com.tencent.wechat"), entry("企业微信")]) as fx:
        out = builtin.app_launch({"target": "微信"})
        assert out.startswith("[ok] 已从开始菜单启动"), out
        assert "微信" in out
        assert fx.launched and fx.launched[0].name == "微信", fx.launched


@case
def launch_reports_the_matched_name_when_not_exact():
    with Fixture([entry("Adobe Photoshop 2024", "D:/PS/Photoshop.exe")]) as fx:
        out = builtin.app_launch({"target": "photoshop"})
        assert out.startswith("[ok] 已从开始菜单启动"), out
        assert "Adobe Photoshop 2024" in out, out
        assert fx.launched[0].name == "Adobe Photoshop 2024"


@case
def launch_suggests_when_nothing_matches():
    with Fixture([entry("微信"), entry("企业微信")]) as fx:
        out = builtin.app_launch({"target": "钉钉"})
        assert out.startswith("[error] 找不到「钉钉」"), out
        assert not fx.launched, "没匹配到就不该启动任何东西"


@case
def alias_still_wins_over_start_menu():
    with Fixture([entry("记事本")]) as fx:
        out = builtin.app_launch({"target": "notepad"})
        assert out.startswith("[stub] exec"), out
        assert not fx.launched, "别名应该直接走 exe，不该去开始菜单"


@case
def missing_target_is_an_error():
    assert builtin.app_launch({}).startswith("[error]")


# ---------- app_list ----------


@case
def app_list_filters_and_counts():
    with Fixture([entry("微信"), entry("微信开发者工具"), entry("QQ"), entry("微信卸载")]):
        out = builtin.app_list({"query": "微信"})
        assert "微信" in out and "开发者工具" in out, out
        assert "微信卸载" not in out, out
        assert "app_launch" in out, out


@case
def app_list_without_query_lists_some():
    with Fixture([entry("微信"), entry("QQ"), entry("钉钉")]):
        out = builtin.app_list({})
        assert out.startswith("[ok]"), out
        for name in ("微信", "QQ", "钉钉"):
            assert name in out, out


@case
def app_list_limit_is_capped():
    many = [entry(f"程序{i:03d}") for i in range(200)]
    with Fixture(many):
        out = builtin.app_list({"limit": 999})
        listed = [line for line in out.splitlines() if line.strip().startswith("- ")]
        assert len(listed) == 80, len(listed)


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
