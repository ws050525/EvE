"""计划工具：让 agent 在长任务里有一份"看得见的待办清单"。

为什么要这个：复杂任务（查资料 → 写正文 → 生成 PPT → 自检）动辄十几步，
模型走到一半很容易忘了还剩什么、或者回头重做已经做完的。给它一个可以反复
读写的清单，等同于把"计划"从上下文里搬进一个不会被摘要掉的地方——
每次调用都把完整清单回给它，等于每一步都重新对齐一次目标。

规则只有一条硬要求：同一时刻最多一个 in_progress。同时开三条线是跑偏的开始。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .registry import ToolRegistry, ToolSpec

STATUSES = ("pending", "in_progress", "completed")
ICONS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
MAX_ITEMS = 20


@dataclass
class TodoItem:
    content: str
    status: str = "pending"


class TodoList:
    """一次会话里的一份清单。工具处理器持有它，跨步复用。"""

    def __init__(self) -> None:
        self.items: list[TodoItem] = []
        self.version = 0  # 每改一次 +1，界面可以据此刷新

    def reset(self) -> None:
        self.items = []
        self.version += 1

    def write(self, raw: Any) -> str:
        items = self._parse(raw)
        if items is None:
            return "[error] items 必须是数组，每项 {content, status}"

        # 同时只允许一个进行中：多出来的降回 pending，并明确告诉模型
        demoted = []
        seen_active = False
        for item in items:
            if item.status == "in_progress":
                if seen_active:
                    demoted.append(item.content)
                    item.status = "pending"
                else:
                    seen_active = True

        self.items = items
        self.version += 1
        text = self.render()
        if demoted:
            text += (
                "\n[note] 同时只能有一个 in_progress，下面这些已自动改回 pending："
                + "、".join(demoted[:3])
            )
        return text

    def _parse(self, raw: Any) -> list[TodoItem] | None:
        if raw is None:
            return None
        if not isinstance(raw, list):
            return None
        out: list[TodoItem] = []
        for entry in raw[:MAX_ITEMS]:
            if isinstance(entry, str):
                content, status = entry, "pending"
            elif isinstance(entry, dict):
                content = str(entry.get("content") or entry.get("text") or "").strip()
                status = str(entry.get("status") or "pending").strip().lower()
            else:
                continue
            if not content:
                continue
            if status not in STATUSES:
                status = "pending"
            out.append(TodoItem(content=content, status=status))
        return out

    def render(self) -> str:
        if not self.items:
            return "[ok] 计划已清空。"

        done = sum(1 for item in self.items if item.status == "completed")
        lines = [f"[ok] 计划（{done}/{len(self.items)} 完成）"]
        current = ""
        for index, item in enumerate(self.items, 1):
            lines.append(f"  {ICONS[item.status]} {index}. {item.content}")
            if item.status == "in_progress" and not current:
                current = f"{index}. {item.content}"
        if current:
            lines.append(f"下一步：{current}")
        elif done < len(self.items):
            lines.append("下一步：挑一项标成 in_progress 再动手。")
        else:
            lines.append("全部完成。可以交付了。")
        return "\n".join(lines)

    def summary(self) -> str:
        """给界面/日志用的一行摘要。"""
        if not self.items:
            return ""
        done = sum(1 for item in self.items if item.status == "completed")
        current = next(
            (item.content for item in self.items if item.status == "in_progress"), ""
        )
        tail = f" · {current}" if current else ""
        return f"{done}/{len(self.items)}{tail}"


def register_planning(registry: ToolRegistry, cfg=None) -> TodoList:
    todos = TodoList()
    # 挂在 registry 上，Agent 才能读出当前计划（存断点、给界面看进度）
    registry.todo = todos

    registry.register(
        ToolSpec(
            name="todo_write",
            description=(
                "维护当前任务的待办清单。多步任务（查资料、写文章、做 PPT、改代码）开始前先列 3–8 步，"
                "每完成一步就更新一次状态：pending / in_progress / completed。"
                "每次调用都要传**完整**清单（不是增量），传 [] 表示清空。"
                "同一时刻只允许一个 in_progress。简单一问一答别用这个工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "完整清单，按执行顺序",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "这一步要做什么"},
                                "status": {
                                    "type": "string",
                                    "enum": list(STATUSES),
                                    "description": "默认 pending",
                                },
                            },
                            "required": ["content"],
                        },
                    }
                },
                "required": ["items"],
            },
            handler=lambda args: todos.write(args.get("items")),
            risk="low",
        )
    )
    return todos
