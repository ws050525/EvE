"""任务断点：把"停在哪"存下来，下次能接着做。

为什么需要它：复杂任务动辄二三十步。用户中途按停止、或者撞上步数上限，
现在的行为是"啥也没了"——上下文还在，但**模型不知道上次干到哪**，只能重头再来，
前面查的资料、写好的两节全白费。

这里只做三件事：

1. `build()`  从 agent 现场抓一份快照（原始请求、计划、动过的文件、消息历史）；
2. `save()`   落到 `.data/tasks/latest.json`，只留最近一份；
3. `brief()`  生成一段给模型看的"上次进度"，续跑时贴在用户消息前面。

消息历史会截断后保存（单条最多 4000 字符）：工具输出动辄上万字，
原样存下来既没必要，也会让续跑时的第一步就撞上下文预算。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

TASKS_DIR = "tasks"
LATEST = "latest.json"
MAX_MESSAGE_CHARS = 4000
MAX_MESSAGES = 40
MAX_FILES = 30

WRITE_TOOLS = {"fs_write", "office_write"}


@dataclass
class TaskCheckpoint:
    id: str = ""
    created: str = ""
    updated: str = ""
    mode: str = ""
    model: str = ""
    reason: str = ""  # cancelled | steps_limit | error
    step: int = 0
    max_steps: int = 0
    request: str = ""
    todo: list[dict[str, str]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def done_steps(self) -> int:
        return sum(1 for item in self.todo if item.get("status") == "completed")

    @property
    def open_steps(self) -> list[str]:
        return [
            str(item.get("content") or "")
            for item in self.todo
            if item.get("status") != "completed"
        ]

    @property
    def current_step(self) -> str:
        for item in self.todo:
            if item.get("status") == "in_progress":
                return str(item.get("content") or "")
        return ""

    def brief(self) -> str:
        """给模型看的续跑说明。要短，但必须把"做到哪"和"还差什么"说清楚。"""
        bits: list[str] = ["（续跑）这是上次没做完的任务，接着做，不要从头再来。"]
        if self.request:
            bits.append(f"原始要求：{self.request[:300]}")
        if self.reason == "steps_limit":
            bits.append(f"上次是撞到步数上限（{self.max_steps} 步）停的。")
        elif self.reason == "cancelled":
            bits.append("上次是用户手动停止的。")
        elif self.reason == "error":
            bits.append("上次是出错停的，先确认错误还存不存在。")

        if self.todo:
            lines = []
            for item in self.todo:
                mark = {"completed": "x", "in_progress": "~"}.get(
                    str(item.get("status") or ""), " "
                )
                lines.append(f"  [{mark}] {item.get('content')}")
            bits.append(f"上次的计划（{self.done_steps}/{len(self.todo)} 完成）：\n" + "\n".join(lines))
        if self.files:
            bits.append("已经产出/改过的文件：" + "、".join(self.files[:MAX_FILES]))
        tail = self._last_assistant()
        if tail:
            bits.append(f"上次说到：{tail[:300]}")
        bits.append("先看一眼现状（计划、已有文件），再从没完成的那一步继续。")
        return "\n".join(bits)

    def _last_assistant(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "assistant" and message.get("content"):
                return str(message["content"]).strip()
        return ""


# ---------- 抓快照 ----------


def _trim(message: dict[str, Any]) -> dict[str, Any]:
    """只留 OpenAI 消息格式需要的字段，长内容截断。"""
    out: dict[str, Any] = {"role": message.get("role", "")}
    content = message.get("content")
    if content:
        text = str(content)
        out["content"] = (
            text if len(text) <= MAX_MESSAGE_CHARS else text[:MAX_MESSAGE_CHARS] + "…（截断）"
        )
    if message.get("tool_calls"):
        out["tool_calls"] = message["tool_calls"]
    if message.get("tool_call_id"):
        out["tool_call_id"] = message["tool_call_id"]
    if message.get("name"):
        out["name"] = message["name"]
    return out


def _files_touched(messages: list[dict[str, Any]]) -> list[str]:
    """从工具调用里扒出写过哪些文件（按出现顺序去重）。"""
    seen: list[str] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") not in WRITE_TOOLS:
                continue
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            path = str(args.get("path") or "").strip()
            if path and path not in seen:
                seen.append(path)
    return seen[-MAX_FILES:]


def build(
    *,
    mode: str,
    model: str,
    reason: str,
    step: int,
    max_steps: int,
    request: str,
    todo: list[dict[str, str]],
    messages: list[dict[str, Any]],
    previous: TaskCheckpoint | None = None,
) -> TaskCheckpoint:
    now = datetime.now().isoformat(timespec="seconds")
    kept = [_trim(m) for m in messages if m.get("role") != "system"][-MAX_MESSAGES:]
    return TaskCheckpoint(
        # 同一秒里可能起好几个任务，纯时间戳会撞车，加一小段随机后缀
        id=(previous.id if previous and previous.id else f"task-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"),
        created=(previous.created if previous and previous.created else now),
        updated=now,
        mode=mode,
        model=model,
        reason=reason,
        step=step,
        max_steps=max_steps,
        request=request,
        todo=list(todo),
        files=_files_touched(messages),
        messages=kept,
    )


# ---------- 落盘 ----------


def _path(data_dir: Path, task_id: str | None = None) -> Path:
    folder = Path(data_dir) / TASKS_DIR
    if task_id:
        safe = "".join(ch for ch in task_id if ch.isalnum() or ch in "-_") or "task"
        return folder / f"{safe}.json"
    return folder / LATEST


def save(checkpoint: TaskCheckpoint, data_dir: Path) -> Path:
    """两处都写：`tasks/<id>.json` 是这条任务自己的存档，`latest.json` 是最新的那份。

    多份存档是为了"断点列表"——同时跑的几个任务各停各的，互不覆盖。
    """
    payload = {
        "version": 1,
        "id": checkpoint.id,
        "created": checkpoint.created,
        "updated": checkpoint.updated,
        "mode": checkpoint.mode,
        "model": checkpoint.model,
        "reason": checkpoint.reason,
        "step": checkpoint.step,
        "max_steps": checkpoint.max_steps,
        "request": checkpoint.request,
        "todo": checkpoint.todo,
        "files": checkpoint.files,
        "messages": checkpoint.messages,
    }
    text = json.dumps(payload, ensure_ascii=False)

    folder = Path(data_dir) / TASKS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    per_task = _path(data_dir, checkpoint.id)
    per_task.write_text(text, encoding="utf-8")

    latest = _path(data_dir)
    latest.write_text(text, encoding="utf-8")
    return per_task


def _from_raw(raw: dict) -> TaskCheckpoint:
    return TaskCheckpoint(
        id=str(raw.get("id") or ""),
        created=str(raw.get("created") or ""),
        updated=str(raw.get("updated") or ""),
        mode=str(raw.get("mode") or ""),
        model=str(raw.get("model") or ""),
        reason=str(raw.get("reason") or ""),
        step=int(raw.get("step") or 0),
        max_steps=int(raw.get("max_steps") or 0),
        request=str(raw.get("request") or ""),
        todo=[item for item in raw.get("todo") or [] if isinstance(item, dict)],
        files=[str(item) for item in raw.get("files") or []],
        messages=[item for item in raw.get("messages") or [] if isinstance(item, dict)],
    )


def load(data_dir: Path, task_id: str | None = None) -> TaskCheckpoint | None:
    """不给 id 就取最新那份；给了 id 就取那条任务自己的存档。"""
    path = _path(data_dir, task_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _from_raw(raw)


def list_all(data_dir: Path, limit: int = 20) -> list[TaskCheckpoint]:
    """所有存下来的断点，最近改过的在前。用来在界面里列出来挑一个继续。"""
    folder = Path(data_dir) / TASKS_DIR
    if not folder.is_dir():
        return []
    found: list[TaskCheckpoint] = []
    for path in folder.glob("*.json"):
        if path.name == LATEST:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            found.append(_from_raw(raw))
    found.sort(key=lambda item: item.updated or "", reverse=True)
    return found[:limit]


def clear(data_dir: Path, task_id: str | None = None) -> None:
    """清掉最新指针；给了 id 就只删那一条，不给就把所有存档都删掉。"""
    for path in [_path(data_dir)]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    folder = Path(data_dir) / TASKS_DIR
    if task_id:
        try:
            _path(data_dir, task_id).unlink(missing_ok=True)
        except OSError:
            pass
        return
    if not folder.is_dir():
        return
    for path in folder.glob("*.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
