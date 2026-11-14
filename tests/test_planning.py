"""计划工具与界面偏好的测试。

todo_write 是复杂任务不跑偏的关键：清单要能反复读写、同一时刻只允许一个进行中、
清单本身要能完整回给模型。偏好持久化测的是"完全权限"这类开关关掉之后下次还在。

跑法： & 'D:\\python\\python.exe' tests\\test_planning.py
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent.tools.planning import TodoList, register_planning
from eve_agent.tools.registry import ToolRegistry, build_default_registry
from eve_core.config import _apply_prefs, load_config, save_preference

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def checklist_renders_progress():
    todos = TodoList()
    out = todos.write(
        [
            {"content": "查资料", "status": "completed"},
            {"content": "写初稿", "status": "in_progress"},
            {"content": "做 PPT"},
        ]
    )
    assert "1/3" in out, out
    assert "[x] 1. 查资料" in out, out
    assert "[~] 2. 写初稿" in out, out
    assert "下一步：2. 写初稿" in out, out


@case
def only_one_item_can_be_in_progress():
    todos = TodoList()
    out = todos.write(
        [
            {"content": "甲", "status": "in_progress"},
            {"content": "乙", "status": "in_progress"},
        ]
    )
    assert [item.status for item in todos.items] == ["in_progress", "pending"], todos.items
    assert "只能有一个" in out, out


@case
def writing_the_same_list_again_replaces_it():
    todos = TodoList()
    todos.write([{"content": "甲", "status": "completed"}])
    version = todos.version
    todos.write([{"content": "乙"}])
    assert len(todos.items) == 1 and todos.items[0].content == "乙"
    assert todos.version > version


@case
def empty_list_clears_the_plan():
    todos = TodoList()
    todos.write([{"content": "甲"}])
    out = todos.write([])
    assert todos.items == []
    assert "清空" in out, out


@case
def bad_input_is_rejected_without_losing_the_plan():
    todos = TodoList()
    todos.write([{"content": "甲"}])
    out = todos.write("不是数组")
    assert out.startswith("[error]"), out
    assert todos.items and todos.items[0].content == "甲", "写坏了不该把旧清单搞没"


@case
def strings_are_accepted_as_items():
    todos = TodoList()
    todos.write(["第一步", "第二步"])
    assert [item.content for item in todos.items] == ["第一步", "第二步"]
    assert all(item.status == "pending" for item in todos.items)


@case
def registry_exposes_todo_tool():
    registry = ToolRegistry()
    register_planning(registry)
    assert registry.has("todo_write")
    out = registry.dispatch(
        "todo_write",
        {"items": [{"content": "写文章"}]},
        allowed={"todo_write"},
    )
    assert "写文章" in out, out


@case
def work_mode_gets_planning_and_bigger_limits():
    """复杂任务要靠 todo_write + 更大的输出/步数上限，缺一个都跑不动。"""
    cfg = load_config()
    work = cfg.modes["work"]
    assert "todo_write" in work.tools, work.tools
    assert work.max_tokens >= 4096, work.max_tokens
    assert work.max_steps >= 20, work.max_steps
    registry: ToolRegistry = build_default_registry(cfg)
    missing = [name for name in work.tools if not registry.has(name)]
    assert not missing, missing


@case
def mode_steps_override_the_global_default():
    from eve_agent.loop import Agent

    cfg = load_config()
    agent = Agent(cfg, "work")
    assert agent.max_steps == cfg.modes["work"].max_steps, (
        agent.max_steps,
        cfg.modes["work"].max_steps,
    )
    agent.set_max_steps(5)
    assert agent.max_steps == 5
    agent.switch_mode("work")
    assert agent.max_steps == cfg.modes["work"].max_steps, "切档应该回到档位自己的步数"


@case
def preferences_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        save_preference(data_dir, auto_confirm=True, max_steps=33, nonsense="x")

        raw = json.loads((data_dir / "prefs.json").read_text(encoding="utf-8"))
        assert raw.get("auto_confirm") is True and raw.get("max_steps") == 33, raw
        assert "nonsense" not in raw, raw

        cfg = SimpleNamespace(data_dir=data_dir, auto_confirm=False, max_steps=12)
        _apply_prefs(cfg)
        assert cfg.auto_confirm is True
        assert cfg.max_steps == 33


@case
def missing_preferences_leave_defaults_alone():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = SimpleNamespace(data_dir=Path(tmp), auto_confirm=False, max_steps=12)
        _apply_prefs(cfg)
        assert cfg.auto_confirm is False and cfg.max_steps == 12


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
