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
    """冒充模型：chat() 供摘要与带工具的档位用，stream() 供无工具档位用。"""

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
            usage={"prompt_tokens": 1000, "completion_tokens": 100},
            latency_ms=1,
        )

    def stream(self, messages, tools=None, *, max_tokens=512, temperature=0.7, on_usage=None):
        self.calls.append((messages, tools, max_tokens))
        if on_usage is not None:
            on_usage(
                {"prompt_tokens": 500, "completion_tokens": 50, "prompt_cache_hit_tokens": 200}
            )
        yield "好的"
        yield "呀"


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
def cancel_stops_before_the_next_tool_runs():
    """按停止之后，不该再执行任何工具，也不该继续往下跑。"""
    from eve_core.provider import ChatResult, ToolCall

    agent = _agent("work")

    class CancellingProvider:
        name = "stub"
        model = "stub"

        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, *, max_tokens=512, temperature=0.7):
            self.calls += 1
            agent.cancel()  # 用户在这一步按了停止
            return ChatResult(
                content="",
                model="stub",
                tool_calls=[
                    ToolCall(id="1", name="fs_write", arguments={"path": "x.txt", "content": "会不会被执行"})
                ],
            )

    provider = CancellingProvider()
    agent._provider = provider
    agent._provider_model_id = agent.model_id

    out = agent.send("写一个文件")

    assert "已停止" in out, out
    assert provider.calls == 1, provider.calls
    assert not (agent.cfg.work_root / "x.txt").exists(), "停止之后工具不该真的执行"


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


@case
def streaming_tier_records_usage():
    agent = _agent("chat")  # chat 档没有工具 → 走流式
    events = []
    agent.bus.subscribe(lambda ev: events.append(ev.kind))
    reply = agent.send("你好")
    assert reply

    last = agent.usage.last
    assert last is not None, "流式也要记账"
    assert last.usage.prompt == 500 and last.usage.cached == 200, last
    assert "usage" in events and "reply_done" in events, events
    assert agent.usage.totals().calls == 1


@case
def cloud_cost_matches_config_price():
    agent = _agent("work")
    assert agent.model_id == "deepseek-flash", agent.model_id  # work 档默认用 flash

    agent.send("帮我看看这个文件")

    totals = agent.usage.totals()
    assert totals.calls == 1, totals

    price = agent.price
    assert not price.free, "云端模型必须配了单价，否则算出来的花费是 0"
    expected = (1000 * price.input + 100 * price.output) / 1_000_000
    assert abs(totals.cost - expected) < 1e-9, (totals.cost, expected)


@case
def local_model_is_free():
    agent = _agent("chat")  # 聊天档跑本地模型 → 不计费
    agent.send("你好")
    assert agent.usage.totals().calls == 1
    assert agent.usage.session_cost == 0.0, agent.usage.session_cost


@case
def usage_survives_mode_switch():
    agent = _agent("chat")
    agent.send("你好")
    before = agent.usage.totals().calls
    agent.switch_mode("work")
    assert agent.usage.totals().calls == before, "切档不该清空账本"


@case
def context_budget_defaults_to_auto():
    agent = _agent("chat")  # 本地 8192，留 512 输出和 512 余量
    budget = agent.context_policy.max_prompt_tokens
    assert agent.context_setting == 0, agent.context_setting
    assert 6000 <= budget <= 8000, budget


@case
def mode_can_override_budget():
    cfg = load_config()
    agent = _agent("work")  # work 档在 config.toml 里单独写了 context_tokens
    expected = cfg.modes["work"].context_tokens
    assert expected > 0, "这段测试依赖 work 档自带的预算"
    assert agent.context_policy.max_prompt_tokens == expected, (
        agent.context_policy.max_prompt_tokens,
        expected,
    )


@case
def runtime_budget_override_wins_and_resets():
    agent = _agent("chat")
    auto = agent.context_policy.max_prompt_tokens

    agent.set_context_limit(4000)
    assert agent.context_setting == 4000
    assert agent.context_policy.max_prompt_tokens == 4000

    agent.set_context_limit(0)
    assert agent.context_setting == 0
    assert agent.context_policy.max_prompt_tokens == auto


@case
def budget_never_exceeds_model_window():
    agent = _agent("chat")
    agent.set_context_limit(24000)  # 远超本地 8192 窗口
    limit = agent.context_policy.max_prompt_tokens
    assert limit < 8192, limit
    assert limit == 8192 - agent.mode.max_tokens - 128, limit


@case
def max_steps_comes_from_config_and_is_clamped():
    from eve_agent.loop import MAX_STEPS_CAP

    agent = _agent("chat")
    assert agent.max_steps == 12, agent.max_steps  # config.toml 里 max_steps = 12

    agent.set_max_steps(30)
    assert agent.max_steps == 30

    agent.set_max_steps(999)
    assert agent.max_steps == MAX_STEPS_CAP, agent.max_steps

    agent.set_max_steps(-5)
    assert agent.max_steps == 1, agent.max_steps


@case
def auto_confirm_skips_the_prompt():
    """最高权限：打开后连高风险工具都不再询问。"""
    from eve_agent.tools.registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    calls = []
    registry.register(
        ToolSpec(
            name="risky",
            description="测试用高风险工具",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: calls.append(1) or "[ok] 执行了",
            risk="high",
        )
    )

    asked = []

    def confirm(spec, args):
        asked.append(spec.name)
        return True

    ok = registry.dispatch(
        "risky", {}, allowed={"risky"}, confirm=confirm, require_confirm=True
    )
    assert asked == ["risky"], asked
    assert ok == "[ok] 执行了", ok

    asked.clear()
    auto = registry.dispatch(
        "risky",
        {},
        allowed={"risky"},
        confirm=confirm,
        require_confirm=True,
        auto_ok=True,
    )
    assert asked == [], "最高权限下不该再询问"
    assert auto == "[ok] 执行了", auto
    assert len(calls) == 2


@case
def model_rows_flag_prices_and_active():
    from eve_ui.bridge import model_rows

    cfg = load_config()
    rows = model_rows(cfg, "deepseek-flash")
    ids = [row["id"] for row in rows]
    assert ids.index("qwen3vl-4b") < ids.index("deepseek-flash"), f"本地应排前面：{ids}"

    flash = next(row for row in rows if row["id"] == "deepseek-flash")
    assert flash["active"] and not flash["free"], flash
    assert flash["price_in"] == 2.0 and flash["price_out"] == 8.0, flash

    local = next(row for row in rows if row["id"] == "qwen3vl-4b")
    assert local["free"] and not local["active"], local


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
