"""人格状态机的单元测试。不需要模型，纯逻辑。

跑法： & 'D:\\python\\python.exe' tests\\test_persona.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.persona import KAOMOJI, Persona

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def command_detection():
    p = Persona()
    assert p._is_command("打开记事本")
    assert p._is_command("帮我打开微信")
    assert p._is_command("search weather")
    assert not p._is_command("打开这个是什么意思？")  # 问句不算命令
    assert not p._is_command("今天天气怎么样")
    assert not p._is_command("帮我写一个很长的东西，长到超过二十四个字符的那种复杂请求吧")


@case
def escalation_levels():
    p = Persona()
    for _ in range(3):
        p.observe_user("打开记事本")
    assert p.protest_level == 1, p.protest_level
    for _ in range(2):
        p.observe_user("搜索天气")
    assert p.protest_level == 2, p.protest_level
    for _ in range(2):
        p.observe_user("打开计算器")
    assert p.protest_level == 3, p.protest_level
    assert not p.allow_tools


@case
def apology_resets():
    p = Persona()
    for _ in range(5):
        p.observe_user("打开记事本")
    assert p.protest_level >= 2
    p.observe_user("对不起，刚才是我急了")
    assert p.protest_level == 0
    assert p.allow_tools


@case
def normal_chat_resets():
    p = Persona()
    for _ in range(3):
        p.observe_user("打开记事本")
    assert p.protest_level == 1
    p.observe_user("你最近好吗")
    assert p.protest_level == 0


@case
def kaomoji_stripped_in_work():
    p = Persona()
    out = p.decorate("改好了" + KAOMOJI["happy"], mode="work")
    assert KAOMOJI["happy"] not in out, out


@case
def kaomoji_never_twice_in_a_row():
    p = Persona()
    first = p.decorate("好的" + KAOMOJI["calm"])
    assert KAOMOJI["calm"] in first
    second = p.decorate("收到" + KAOMOJI["calm"])
    assert KAOMOJI["calm"] not in second, second


@case
def kaomoji_stripped_on_long_reply():
    p = Persona()
    text = "字" * 250 + KAOMOJI["happy"]
    assert KAOMOJI["happy"] not in p.decorate(text)


@case
def kaomoji_kept_in_normal_chat():
    p = Persona()
    text = "这个问题非常简单" + KAOMOJI["smug"]
    assert KAOMOJI["smug"] in p.decorate(text, mode="chat")


@case
def kaomoji_only_one_per_reply():
    p = Persona()
    text = "好的" + KAOMOJI["calm"] + "嗯" + KAOMOJI["awkward"]
    out = p.decorate(text, mode="chat")
    kept = [k for k in KAOMOJI.values() if k in out]
    assert len(kept) == 1, out
    assert kept[0] == KAOMOJI["awkward"], out  # 保留最后出现的


@case
def prompt_extra_tracks_level():
    p = Persona()
    assert p.prompt_extra() == ""
    for _ in range(3):
        p.observe_user("打开记事本")
    assert "不爽" in p.prompt_extra()


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
