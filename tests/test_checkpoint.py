"""断点续跑的测试：停在哪能存下来、下次能不能接着做。

跑法： & 'D:\\python\\python.exe' tests\\test_checkpoint.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent import checkpoint as checkpoints
from eve_agent.loop import Agent
from eve_core.config import load_config
from eve_core.provider import ChatResult, ToolCall

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


def _messages_with_writes():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "写一套资料"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "fs_write",
                        "arguments": '{"path": "docs/a.md", "content": "x"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "1", "name": "fs_write", "content": "ok"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "2",
                    "type": "function",
                    "function": {
                        "name": "office_write",
                        "arguments": '{"kind": "docx", "path": "docs/b.docx"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "2", "name": "office_write", "content": "ok"},
    ]


@case
def build_collects_written_files():
    cp = checkpoints.build(
        mode="work",
        model="deepseek-flash",
        reason="cancelled",
        step=4,
        max_steps=32,
        request="写一套资料",
        todo=[{"content": "写主报告", "status": "in_progress"}],
        messages=_messages_with_writes(),
    )
    assert cp.files == ["docs/a.md", "docs/b.docx"], cp.files
    assert cp.done_steps == 0 and cp.open_steps == ["写主报告"], cp.todo
    assert cp.current_step == "写主报告"


@case
def brief_says_where_it_stopped():
    cp = checkpoints.build(
        mode="work",
        model="flash",
        reason="steps_limit",
        step=32,
        max_steps=32,
        request="做一份市场报告",
        todo=[
            {"content": "查资料", "status": "completed"},
            {"content": "写正文", "status": "in_progress"},
            {"content": "做 PPT", "status": "pending"},
        ],
        messages=[{"role": "assistant", "content": "资料查完了，正在写第二节"}],
    )
    text = cp.brief()
    assert "续跑" in text
    assert "做一份市场报告" in text
    assert "查资料" in text and "做 PPT" in text
    assert "1/3" in text, text
    assert "步数上限" in text, text
    assert "正在写第二节" in text, text


@case
def save_and_load_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        cp = checkpoints.build(
            mode="work",
            model="flash",
            reason="cancelled",
            step=5,
            max_steps=32,
            request="写文章",
            todo=[{"content": "写", "status": "in_progress"}],
            messages=_messages_with_writes(),
        )
        path = checkpoints.save(cp, data)
        assert path.exists()

        loaded = checkpoints.load(data)
        assert loaded is not None
        assert loaded.request == "写文章"
        assert loaded.reason == "cancelled"
        assert loaded.files == cp.files
        assert loaded.messages and loaded.messages[0]["role"] != "system"

        checkpoints.clear(data)
        assert checkpoints.load(data) is None


@case
def long_messages_are_trimmed_before_saving():
    cp = checkpoints.build(
        mode="work",
        model="flash",
        reason="cancelled",
        step=2,
        max_steps=10,
        request="x",
        todo=[],
        messages=[{"role": "tool", "content": "甲" * 20000}],
    )
    saved = cp.messages[0]["content"]
    assert len(saved) <= checkpoints.MAX_MESSAGE_CHARS + 10, len(saved)
    assert saved.endswith("（截断）")


@case
def no_checkpoint_means_nothing_to_resume():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config()
        cfg.data_dir = Path(tmp)
        agent = Agent(cfg, "work")
        assert agent.resume() is None


@case
def stopping_saves_and_resume_picks_it_up():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config()
        cfg.data_dir = Path(tmp)

        agent = Agent(cfg, "work")

        class CancellingProvider:
            name = "stub"
            model = "stub"

            def chat(self, messages, tools=None, *, max_tokens=512, temperature=0.7):
                agent.cancel()
                return ChatResult(
                    content="",
                    model="stub",
                    tool_calls=[
                        ToolCall(id="1", name="todo_write", arguments={"items": [{"content": "查资料", "status": "in_progress"}, {"content": "写正文", "status": "pending"}]})
                    ],
                )

        agent._provider = CancellingProvider()
        agent._provider_model_id = agent.model_id
        # 先让计划存在（真实场景里模型在更早的一步已经用 todo_write 列过了）
        agent.todo.write(
            [
                {"content": "查资料", "status": "in_progress"},
                {"content": "写正文", "status": "pending"},
            ]
        )

        out = agent.send("做一份市场报告")
        assert "已停止" in out, out
        assert checkpoints.load(cfg.data_dir) is not None, "停止后应该留下断点"

        # 换一个全新的 agent，模拟"关掉再打开"
        fresh = Agent(cfg, "work")
        brief = fresh.resume()

        assert brief and "做一份市场报告" in brief, brief
        assert "查资料" in brief, brief
        # 上下文接上了
        assert any(m.get("role") == "user" for m in fresh.messages), fresh.messages
        # 计划也恢复到 todo 工具里了
        items = fresh.todo_items()
        assert [item["content"] for item in items] == ["查资料", "写正文"], items


@case
def finishing_the_task_clears_the_checkpoint():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config()
        cfg.data_dir = Path(tmp)
        agent = Agent(cfg, "work")

        class DoneProvider:
            name = "stub"
            model = "stub"

            def chat(self, messages, tools=None, *, max_tokens=512, temperature=0.7):
                return ChatResult(content="写完了。", model="stub")

        agent._provider = DoneProvider()
        agent._provider_model_id = agent.model_id

        agent.send("随便来点")
        assert checkpoints.load(cfg.data_dir) is None, "任务做完就不该再留着断点"


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
