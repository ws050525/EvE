"""共享笔记（跨任务黑板）的测试。

跑法： & 'D:\\python\\python.exe' tests\\test_shared.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_agent import shared
from eve_agent.shared import SharedBoard
from eve_agent.tools.registry import ToolRegistry
from eve_agent.tools.shared_tools import register_shared

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def write_then_read_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        board = SharedBoard(Path(tmp))
        out = board.write("市场数据", "2026 年 136 亿美元", source="工作任务")
        assert out.startswith("[ok]"), out

        text = board.read()
        assert "市场数据" in text and "136 亿美元" in text, text
        assert "工作任务" in text, text

        one = board.read("市场数据")
        assert "136 亿美元" in one, one


@case
def rewriting_the_same_title_updates_instead_of_duplicating():
    with tempfile.TemporaryDirectory() as tmp:
        board = SharedBoard(Path(tmp))
        board.write("结论", "第一版")
        board.write("结论", "第二版")
        text = board.read()
        assert "第二版" in text and "第一版" not in text, text
        assert board.count() == 1, board.count()


@case
def forget_removes_one_entry():
    with tempfile.TemporaryDirectory() as tmp:
        board = SharedBoard(Path(tmp))
        board.write("A", "1")
        board.write("B", "2")
        board.forget("A")
        text = board.read()
        assert "B" in text and "## A" not in text, text


@case
def notes_survive_a_new_board_instance():
    """落在 .data/shared.json，重启后还在。"""
    with tempfile.TemporaryDirectory() as tmp:
        first = SharedBoard(Path(tmp))
        first.write("持久化", "重启也要在")

        second = SharedBoard(Path(tmp))
        assert "重启也要在" in second.read(), second.read()


@case
def long_content_is_truncated():
    with tempfile.TemporaryDirectory() as tmp:
        board = SharedBoard(Path(tmp))
        board.write("长文", "甲" * 5000)
        text = board.read("长文")
        assert len(text) < 2600, len(text)
        assert "截断" in text, text[-20:]


@case
def too_many_notes_drops_the_oldest():
    with tempfile.TemporaryDirectory() as tmp:
        board = SharedBoard(Path(tmp))
        for index in range(shared.MAX_NOTES + 3):
            board.write(f"n{index}", f"内容{index}")
        assert board.count() == shared.MAX_NOTES, board.count()
        assert "## n0" not in board.read(), "最旧的应该被挤掉"


@case
def prompt_block_stays_short():
    with tempfile.TemporaryDirectory() as tmp:
        board = SharedBoard(Path(tmp))
        board.write("要点一", "结论" * 800)
        board.write("要点二", "另一条")
        block = board.prompt_block(limit=2, chars=100)
        assert "共享笔记" in block, block
        assert len(block) < 600, len(block)


@case
def source_is_recorded_when_set():
    """工具写笔记时要带上"谁写的"，这样别的任务知道这条结论来自哪。"""
    with tempfile.TemporaryDirectory() as tmp:
        class Cfg:
            data_dir = Path(tmp)

        shared._BOARD = None
        registry = ToolRegistry()
        register_shared(registry, Cfg())
        allowed = set(registry.names())

        shared.set_source("工作·查显卡")
        try:
            registry.dispatch(
                "share_write", {"title": "价格", "content": "4060 约 2200 元"}, allowed=allowed
            )
        finally:
            shared.set_source("")

        text = registry.dispatch("share_read", {}, allowed=allowed)
        assert "工作·查显卡" in text, text
        shared._BOARD = None


@case
def tools_are_registered_and_work():
    with tempfile.TemporaryDirectory() as tmp:
        class Cfg:
            data_dir = Path(tmp)

        shared._BOARD = None  # 让 register_shared 重新绑定到临时目录
        registry = ToolRegistry()
        register_shared(registry, Cfg())
        allowed = set(registry.names())
        assert {"share_write", "share_read", "share_forget"} <= allowed

        out = registry.dispatch(
            "share_write", {"title": "T", "content": "C"}, allowed=allowed
        )
        assert out.startswith("[ok]"), out
        read_back = registry.dispatch("share_read", {}, allowed=allowed)
        assert "C" in read_back, read_back

        registry.dispatch("share_forget", {"title": "T"}, allowed=allowed)
        assert "空" in registry.dispatch("share_read", {}, allowed=allowed)
        shared._BOARD = None


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
