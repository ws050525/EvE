"""任务模型：一个任务 = 一份独立上下文 + 一个聊天视图 + 一个 agent 线程。

为什么要拆多任务：复杂任务动不动几十秒到几分钟，一次只能干一件事实在是浪费。
现在最多同时跑 `MAX_RUNNING` 个任务，各自独立的对话历史和模型上下文，互不干扰。

每个任务都是**完整**的：自己的 AgentBridge（自己的 QThread 和 Agent）、
自己的 ChatView（切任务时整块换，滚动位置都保留）、自己的计划与花费。
切换任务只影响"现在看哪个"，不影响后台在跑的那些。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

MAX_RUNNING = 3  # 同时跑的任务上限。再多就是烧钱和抢上下文了

STATUS_ICONS = {
    "idle": "○",
    "running": "●",
    "done": "✓",
    "error": "✕",
    "paused": "❚❚",
}

STATUS_LABELS = {
    "idle": "空闲",
    "running": "进行中",
    "done": "已完成",
    "error": "出错",
    "paused": "已暂停",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Task:
    id: str
    title: str = "新任务"
    request: str = ""
    mode: str = "chat"
    model: str = ""
    status: str = "idle"
    # UI/运行时对象（由主窗注入）
    chat: object = None
    bridge: object = None
    # 每个任务自己的流式状态，别让它跟别的任务串
    streaming: bool = False
    tool_row: object = None
    busy: bool = False
    started_chat: bool = False
    # 账本（按任务分开看）
    last_usage: dict = field(default_factory=dict)
    cost: float = 0.0
    last_latency: int = 0
    created: str = field(default_factory=_now)
    updated: str = field(default_factory=_now)

    @property
    def icon(self) -> str:
        return STATUS_ICONS.get(self.status, "○")

    @property
    def short_title(self) -> str:
        text = (self.title or self.request or "新任务").strip().replace("\n", " ")
        return text if len(text) <= 12 else text[:12] + "…"

    def touch(self) -> None:
        self.updated = _now()

    def set_status(self, status: str) -> None:
        self.status = status
        self.touch()
