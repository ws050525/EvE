"""共享笔记工具：让并行任务之间能传结论。

三个工具就够了：写一条、读（一条或全部）、删一条。
正文上限 2000 字——这里是"结论"的位置，长文该落成文件再在笔记里写路径。
"""

from __future__ import annotations

from typing import Any

from .registry import ToolRegistry, ToolSpec
from .. import shared


def _write(args: dict[str, Any], board: shared.SharedBoard) -> str:
    return board.write(
        str(args.get("title") or ""),
        str(args.get("content") or ""),
        source=shared.current_source(),
    )


def _read(args: dict[str, Any], board: shared.SharedBoard) -> str:
    return board.read(str(args.get("title") or ""))


def _forget(args: dict[str, Any], board: shared.SharedBoard) -> str:
    return board.forget(str(args.get("title") or ""))


def register_shared(registry: ToolRegistry, cfg=None) -> None:
    board = shared.board_for(getattr(cfg, "data_dir", None) if cfg else None)

    registry.register(
        ToolSpec(
            name="share_write",
            description=(
                "把一条**结论**写进共享黑板，别的任务（包括以后新开的）都能读到。"
                "多任务并行时用它交接：查到的数据、定下的格式、用户的偏好。"
                "正文控制在 2000 字内，写结论和关键数据，不要贴全文——全文落成文件，这里只写路径。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "短标题，用来区分条目"},
                    "content": {"type": "string", "description": "结论正文，≤2000 字"},
                },
                "required": ["title", "content"],
            },
            handler=lambda args: _write(args, board),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="share_read",
            description=(
                "读共享黑板。不给 title 就把所有条目读出来（最近更新的在前）。"
                "每个任务开头都可以先读一眼，看看别人已经做过什么，别重复劳动。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "要读的条目标题，可省"}
                },
            },
            handler=lambda args: _read(args, board),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="share_forget",
            description="删掉共享黑板里的一条（结论过时了、写错了）。",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string", "description": "条目标题"}},
                "required": ["title"],
            },
            handler=lambda args: _forget(args, board),
            risk="low",
        )
    )
