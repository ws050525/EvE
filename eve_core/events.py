"""事件通道：agent 干活时对外播报"我在干什么"。

设计原则：
- 核心层（eve_core）只负责发事件，不关心谁来听、怎么显示。
- 订阅者出错绝不能影响主流程，所以每个回调都包了 try。
- 事件的 kind 是稳定的字符串常量，UI 和 CLI 共用同一套。

未来 PySide6 那边只要把一个 handler 接到 Qt Signal 上即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[["Event"], None]

# 事件类型（保持稳定，UI 依赖这些名字）
ENGINE_STARTING = "engine_starting"
ENGINE_READY = "engine_ready"
ENGINE_STOPPED = "engine_stopped"
USER_MESSAGE = "user_message"
THINKING = "thinking"
TOOL_START = "tool_start"
TOOL_END = "tool_end"
REPLY_DONE = "reply_done"
ERROR = "error"

KNOWN_KINDS = (
    ENGINE_STARTING,
    ENGINE_READY,
    ENGINE_STOPPED,
    USER_MESSAGE,
    THINKING,
    TOOL_START,
    TOOL_END,
    REPLY_DONE,
    ERROR,
)


@dataclass
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """极简发布订阅。够用就好，不引第三方。"""

    def __init__(self) -> None:
        self._all: list[Handler] = []
        self._by_kind: dict[str, list[Handler]] = {}

    def subscribe(self, handler: Handler, kind: str | None = None) -> None:
        if kind is None:
            self._all.append(handler)
        else:
            self._by_kind.setdefault(kind, []).append(handler)

    def emit(self, kind: str, **data: Any) -> None:
        event = Event(kind, data)
        for handler in list(self._all):
            try:
                handler(event)
            except Exception:  # 订阅者的 bug 不能拖垮对话
                pass
        for handler in list(self._by_kind.get(kind, ())):
            try:
                handler(event)
            except Exception:
                pass
