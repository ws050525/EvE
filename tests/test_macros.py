"""语义动作记忆的测试：存取、匹配、回放、失败即停。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.macros import MIN_SCORE, MacroStore, fill_slots, replay

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


STEPS = [
    {"do": "launch", "app": "网易云音乐"},
    {"do": "type", "target": "搜索框", "text": "晴天"},
    {"do": "key", "keys": "enter"},
]


@case
def add_and_reload_from_disk():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        store.add("网易云搜歌播放", ["网易云", "搜歌", "播放"], STEPS, verify="在播吗")

        again = MacroStore(Path(tmp))
        assert len(again.macros) == 1, again.macros
        macro = again.macros[0]
        assert macro.name == "网易云搜歌播放"
        assert [step.do for step in macro.steps] == ["launch", "type", "key"], macro.steps
        assert macro.verify == "在播吗"


@case
def match_needs_enough_keyword_weight():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        store.add("网易云搜歌播放", ["网易云", "搜歌"], STEPS)

        hit, score = store.match("用网易云搜歌")
        assert hit is not None and score >= MIN_SCORE, (hit, score)

        miss, score = store.match("打开记事本写一段话")
        assert miss is None, (miss, score)


@case
def match_picks_the_most_specific_one():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        store.add("网易云", ["网易云"], STEPS)
        store.add("网易云搜歌", ["网易云", "搜歌", "晴天"], STEPS)

        hit, _score = store.match("在网易云搜歌，歌名晴天")
        assert hit is not None and hit.name == "网易云搜歌", hit


@case
def forget_removes_it():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        store.add("临时", ["临时任务"], STEPS)
        assert store.forget("临时") is True
        assert store.list_all() == []
        assert MacroStore(Path(tmp)).list_all() == []


@case
def replay_translates_intents_into_tool_calls():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        macro = store.add(
            "搜歌",
            ["搜歌"],
            [
                {"do": "launch", "app": "网易云音乐"},
                {"do": "type", "target": "搜索框", "text": "晴天"},
                {"do": "key", "keys": "enter"},
            ],
        )

        calls = []

        def dispatch(tool, args):
            calls.append((tool, args))
            if tool == "ui_find":
                return "[ok] 「搜索框」在 (400,300) …\nmouse_click(420, 310)\n点完确认"
            return "[ok] done"

        ok, lines = replay(macro, dispatch, {"app_launch", "ui_find", "mouse_click", "key_type", "key_hotkey"})

        assert ok is True, lines
        assert calls[0] == ("app_launch", {"target": "网易云音乐"}), calls
        # type = 先定位再点击，再输入
        assert calls[1][0] == "ui_find", calls
        assert calls[2] == ("mouse_click", {"x": 420, "y": 310}), calls
        assert calls[3] == ("key_type", {"text": "晴天"}), calls
        assert calls[4] == ("key_hotkey", {"keys": "enter"}), calls


@case
def replay_stops_at_the_first_failure():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        macro = store.add("坏的", ["坏的"], [{"do": "click", "target": "不存在的按钮"}, {"do": "key", "keys": "enter"}])

        calls = []

        def dispatch(tool, args):
            calls.append(tool)
            if tool == "ui_find":
                return "[error] 三种办法都没找到"
            return "[ok] done"

        ok, lines = replay(macro, dispatch, {"ui_find", "mouse_click", "key_hotkey"})

        assert ok is False
        assert calls == ["ui_find"], calls  # 失败之后不该继续按回车
        assert "没有找到" in lines[-1] or "error" in lines[-1], lines


@case
def replay_refuses_tools_outside_the_mode():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        macro = store.add("越权", ["越权"], [{"do": "launch", "app": "记事本"}])

        ok, lines = replay(macro, lambda tool, args: "[ok]", {"ui_find"})
        assert ok is False and "不在当前档位" in lines[-1], lines


@case
def hits_are_counted():
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        macro = store.add("计数", ["计数"], [{"do": "key", "keys": "enter"}])
        store.record_hit(macro, True)
        store.record_hit(macro, False)
        assert macro.hits == 2, macro.hits
        assert macro.last_ok, "成功过一次就该记时间"


@case
def compound_task_is_not_hijacked():
    """一串任务（然后/接着/最后…）不能被单条记忆吃掉——实测抢过一次。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        store.add("网易云搜歌", ["网易云", "搜歌"], STEPS)

        assert store.match("用网易云搜歌，歌名《晴天》")[0] is not None
        for text in (
            "先在网易云搜歌，然后打开抖音搜同名，最后写进文件",
            "帮我做三件事：第一网易云搜歌，第二抖音，第三写文件",
            "先搜歌，接着播放，最后截图",
        ):
            assert store.match(text)[0] is None, text


@case
def slots_reject_values_that_do_not_look_like_parameters():
    """抽到整段指令（带路径、标点）时宁可不要，也不能拿去输入。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = MacroStore(Path(tmp))
        macro = store.add("搜歌", ["搜歌"], STEPS, patterns=["歌名[：:是为]?\\s*(.+?)$"])

        good = store.add("搜歌2", ["搜歌2"], STEPS, patterns=["《(.+?)》"])
        assert store is not None
        assert fill_slots(good, "搜《晴天》") == {"1": "晴天"}
        bad = fill_slots(macro, "歌名；最后把歌名写进 D:\\1\\EVE\\.tmp\\a.txt，先列出计划")
        assert bad == {}, bad


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
