"""Agent 主循环的集成测试：真配置 + 桩模型。

不拉本地引擎、不联网，只验证接线：
- 档位里的工具都真的存在（不再静默丢工具）
- 上下文超预算时会剪辑历史、写摘要，并且摘要进了 system prompt
- 工具输出入库前会被截断

跑法： & 'D:\\python\\python.exe' tests\\test_agent_loop.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_core.config import load_config
from eve_agent.context import messages_tokens
from eve_agent.loop import Agent

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


class StubProvider:
    """冒充模型：chat() 只用来接摘要请求。"""

    name = "stub"
    model = "stub"

    def __init__(self) -> None:
        self.calls = []

    def chat(self, messages, tools=None, *, max_tokens=512, temperature=0.7):
        from eve_core.provider import ChatResult

        self.calls.append((messages, tools, max_tokens))
        return ChatResult(
            content="（摘要）用户在打磨 EVE 的 agent。",
            model="stub",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            latency_ms=1,
        )


def _agent(mode: str = "chat", **overrides) -> Agent:
    cfg = load_config()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    agent = Agent(cfg, mode)
    stub = StubProvider()
    agent._provider = stub
    agent._provider_model_id = agent.model_id
    return agent


def _fill(agent: Agent, turns: int, chars: int = 200) -> None:
    for i in range(turns):
        agent.messages.append({"role": "user", "content": f"第{i}个问题" + "甲" * chars})
        agent.messages.append({"role": "assistant", "content": f"第{i}个回答" + "乙" * chars})


@case
def work_tier_has_real_tools():
    agent = _agent("work")
    for name in ("fs_read", "fs_write", "fs_list", "shell_exec"):
        assert name in agent.active_tools, f"work 档缺少 {name}：{agent.active_tools}"
    assert agent.warning == "", agent.warning


@case
def desktop_tier_still_complete():
    agent = _agent("desktop")
    assert agent.warning == "", agent.warning
    assert len(agent.active_tools) >= 8, agent.active_tools


@case
def context_compaction_keeps_system_and_appends_summary():
    agent = _agent("chat")
    # 预算要建在 system prompt 之上：system 是底线，永远不会被裁，
    # 预算比它还小的话再怎么压都不可能达标。
    system_tokens = messages_tokens([agent.messages[0]])
    agent.cfg.context_tokens = system_tokens + 400
    _fill(agent, 10)

    used_before, budget = agent.context_usage
    assert used_before > budget, (used_before, budget)

    agent._enforce_context()

    used_after, _ = agent.context_usage
    assert used_after <= budget, (used_after, budget)
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[0]["content"].startswith(agent.cfg.mode("chat").system_prompt.read_text(encoding="utf-8").strip()[:40])
    assert agent.summary, "超预算后应该留下摘要"
    assert "先前对话摘要" in agent._outgoing()[0]["content"]
    assert agent._provider.calls, "应该让模型写过摘要"


@case
def compaction_reports_an_event():
    agent = _agent("chat", context_tokens=300)
    _fill(agent, 10)
    events = []
    agent.bus.subscribe(lambda ev: events.append(ev.kind))
    agent._enforce_context()
    assert "context_compacted" in events, events


@case
def switching_mode_clears_summary():
    agent = _agent("chat", context_tokens=300)
    _fill(agent, 10)
    agent._enforce_context()
    assert agent.summary
    agent.switch_mode("work")
    assert agent.summary == ""
    assert len(agent.messages) == 1


@case
def tool_output_is_capped_on_ingest():
    agent = _agent("chat", tool_output_chars=1000)
    capped = agent._cap_tool_output("x" * 5000)
    assert len(capped) < 1200, len(capped)
    assert "省略" in capped
    assert agent._cap_tool_output("short") == "short"


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
