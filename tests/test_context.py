"""上下文预算的单元测试。纯逻辑，不需要模型。

跑法： & 'D:\\python\\python.exe' tests\\test_context.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.context import (
    ContextPolicy,
    compact,
    estimate_tokens,
    messages_tokens,
    shrink,
    split_turns,
)

CHECKS = []

SYSTEM = {"role": "system", "content": "你是 EVE。"}


def case(fn):
    CHECKS.append(fn)
    return fn


def turn(text: str, reply: str) -> list[dict]:
    return [
        {"role": "user", "content": text},
        {"role": "assistant", "content": reply},
    ]


def tool_turn(text: str, name: str = "fs_read", output: str = "文件内容") -> list[dict]:
    return [
        {"role": "user", "content": text},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": '{"path": "a.txt"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": name, "content": output},
        {"role": "assistant", "content": "读完了"},
    ]


@case
def chinese_costs_more_than_ascii():
    zh = estimate_tokens("你好世界" * 10)
    en = estimate_tokens("hello world " * 5)
    assert zh > en, (zh, en)
    assert estimate_tokens("") == 0


@case
def token_estimate_is_roughly_sane():
    # 40 个汉字大约 40 token；40 个 ASCII 字符约 12 token
    assert 35 <= estimate_tokens("字" * 40) <= 45
    assert 8 <= estimate_tokens("a" * 40) <= 16


@case
def split_turns_keeps_tool_messages_with_their_call():
    messages = [SYSTEM] + turn("你好", "你好呀") + tool_turn("读文件")
    turns = split_turns(messages[1:])
    assert len(turns) == 2, [len(t) for t in turns]
    assert turns[0][0]["role"] == "user"
    assert [m["role"] for m in turns[1]] == ["user", "assistant", "tool", "assistant"]


@case
def no_compaction_when_under_budget():
    messages = [SYSTEM] + turn("你好", "你好呀")
    policy = ContextPolicy(max_prompt_tokens=3500)
    out, summary, report = compact(messages, policy)
    assert out == messages
    assert summary == ""
    assert not report.compacted
    assert report.tokens_before == report.tokens_after


@case
def compacts_oldest_turns_first():
    messages = [SYSTEM]
    for i in range(10):
        messages += turn(f"第{i}个问题", f"第{i}个回答")
    policy = ContextPolicy(max_prompt_tokens=150, keep_recent_turns=2)

    out, summary, report = compact(messages, policy)
    assert report.dropped_turns > 0, report
    assert messages_tokens(out) + estimate_tokens(summary) <= 150, report.tokens_after
    # 最近一轮一定还在
    assert out[-1]["content"] == "第9个回答"
    # 被丢的都是最老的
    kept_users = [m["content"] for m in out if m["role"] == "user"]
    assert "第0个问题" not in kept_users
    assert "第9个问题" in kept_users
    # 没有摘要回调时，摘要里要留一条"省略了 N 轮"的记号
    assert "省略" in summary, summary


@case
def tool_messages_never_orphaned():
    messages = [SYSTEM]
    for i in range(8):
        messages += tool_turn(f"第{i}个任务", output="x" * 400)
    policy = ContextPolicy(max_prompt_tokens=300, keep_recent_turns=1)

    out, _, report = compact(messages, policy)
    assert report.dropped_turns > 0

    # 每条 tool 消息前面必须有带 tool_calls 的 assistant 消息
    pending = set()
    for message in out:
        role = message.get("role")
        if role == "assistant":
            pending = {call["id"] for call in message.get("tool_calls") or []}
        elif role == "tool":
            assert message["tool_call_id"] in pending, message
        elif role == "user":
            assert not pending, "上一轮的 tool_calls 没有跟 tool 结果就被切断了"
    assert out[0]["role"] == "system"


@case
def uses_summary_callback():
    calls = []

    def fake_summary(text: str, max_tokens: int) -> str:
        calls.append((text, max_tokens))
        return "用户在研究上下文裁剪。"

    messages = [SYSTEM]
    for i in range(8):
        messages += turn(f"第{i}个问题", "回答" * 100)
    policy = ContextPolicy(max_prompt_tokens=250, summary_max_tokens=120)

    out, summary, report = compact(messages, policy, summarize=fake_summary)
    assert calls, "应该调用过摘要"
    assert report.summarized
    assert summary == "用户在研究上下文裁剪。"
    assert messages_tokens(out) + estimate_tokens(summary) <= 250


@case
def too_short_summary_is_rejected():
    def echo(text: str, max_tokens: int) -> str:
        return "记住了。"  # 模型跑偏：把记录里的指令当成了对它说的

    messages = [SYSTEM]
    for i in range(8):
        messages += turn(f"第{i}个问题", "回答" * 100)
    policy = ContextPolicy(max_prompt_tokens=250)

    _, summary, report = compact(messages, policy, summarize=echo)
    assert not report.summarized, "这么短的摘要应当按失败处理"
    assert "省略" in summary, summary


@case
def summary_request_is_framed():
    seen = []

    def capture(text: str, max_tokens: int) -> str:
        seen.append(text)
        return "一段足够长的正常摘要内容"

    messages = [SYSTEM]
    for i in range(8):
        messages += turn(f"第{i}个问题", "回答" * 100)
    compact(messages, ContextPolicy(max_prompt_tokens=250), summarize=capture)
    assert seen, "应该调用过摘要"
    assert "【对话记录】" in seen[0], seen[0][:200]


@case
def failed_summary_falls_back_to_note():
    def broken(text: str, max_tokens: int) -> str:
        raise RuntimeError("模型挂了")

    messages = [SYSTEM]
    for i in range(8):
        messages += turn(f"第{i}个问题", "回答" * 100)
    policy = ContextPolicy(max_prompt_tokens=250)

    out, summary, report = compact(messages, policy, summarize=broken)
    assert not report.summarized
    assert "省略" in summary
    assert messages_tokens(out) + estimate_tokens(summary) <= 250


@case
def summary_is_carried_into_next_compaction():
    seen = []

    def fake_summary(text: str, max_tokens: int) -> str:
        seen.append(text)
        return "累计摘要：用户在打磨上下文裁剪。"

    messages = [SYSTEM]
    for i in range(6):
        messages += turn(f"第{i}个问题", "回答" * 80)
    policy = ContextPolicy(max_prompt_tokens=220)
    out, summary, _ = compact(messages, policy, summarize=fake_summary)
    assert summary.startswith("累计摘要"), summary

    more = out + turn("新问题", "新回答" * 80)
    _, summary2, _ = compact(more, policy, summary=summary, summarize=fake_summary)
    assert any("累计摘要" in text for text in seen), "旧摘要要喂给摘要器，否则会丢历史"


@case
def shrink_cuts_the_largest_message():
    messages = [
        SYSTEM,
        {"role": "user", "content": "看这个"},
        {"role": "tool", "tool_call_id": "c1", "name": "http_get", "content": "网" * 4000},
        {"role": "assistant", "content": "好"},
    ]
    out, removed = shrink(messages, 500)
    assert removed > 0
    assert messages_tokens(out) <= 500 or len(out[2]["content"]) < 4000
    assert out[0] is SYSTEM or out[0]["content"] == SYSTEM["content"], "system 不能被截断"


@case
def single_huge_turn_still_fits():
    messages = [
        SYSTEM,
        {"role": "user", "content": "读这个网页"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "http_get", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "http_get", "content": "长" * 20000},
    ]
    policy = ContextPolicy(max_prompt_tokens=800)
    out, summary, report = compact(messages, policy)
    assert messages_tokens(out) + estimate_tokens(summary) <= 800, report.tokens_after
    assert out[-1]["role"] == "tool"


@case
def input_history_is_not_mutated():
    messages = [SYSTEM]
    for i in range(6):
        messages += turn(f"第{i}个问题", "回答" * 100)
    before = messages_tokens(messages)
    compact(messages, ContextPolicy(max_prompt_tokens=200))
    assert messages_tokens(messages) == before, "compact 不能就地改调用方的历史"


@case
def empty_history_is_safe():
    out, summary, report = compact([], ContextPolicy())
    assert out == [] and summary == "" and not report.compacted


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # 意外异常也要报出来
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
