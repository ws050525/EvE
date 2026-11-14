"""跨任务共享的"小黑板"：一个任务写，别的任务读。

为什么需要：多任务并行之后，常见场景是"这个任务查到的结论，另一个任务要用"。
没有共享就得靠人复制粘贴，而这恰恰是最没必要手工做的事。

设计上故意做得很小：

- 键 + 标题 + 正文，正文上限 2000 字（是"结论"，不是"全文"，全文该落文件）；
- 最多 20 条，写满就丢最旧的（避免变成第二个上下文）；
- 落在 `.data/shared.json`，所以**重启也在**——任务条是内存里的，这块黑板不是；
- 每条记来源任务，读的时候能看到"这是谁说的"。

注入方式：`Agent._outgoing()` 会把黑板内容拼进 system prompt 的末尾，
所以每个任务每步都能看到别人的进展，但**不会污染各自的对话历史**。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

SHARED_FILE = "shared.json"
MAX_NOTES = 20
MAX_CONTENT = 2000
MAX_TITLE = 40

_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class SharedNote:
    title: str
    content: str
    source: str = ""
    updated: str = ""


class SharedBoard:
    """进程内唯一的黑板。多线程安全（多个任务线程会同时读写）。"""

    def __init__(self, data_dir: Path | None = None):
        self.path = Path(data_dir) / SHARED_FILE if data_dir else None
        self._notes: dict[str, SharedNote] = {}
        self._loaded = False

    # ---------- 存储 ----------

    def _ensure(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw.get("notes", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            self._notes[title] = SharedNote(
                title=title,
                content=str(item.get("content") or ""),
                source=str(item.get("source") or ""),
                updated=str(item.get("updated") or ""),
            )

    def _flush(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"notes": [asdict(note) for note in self._notes.values()]}
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ---------- 读写 ----------

    def write(self, title: str, content: str, source: str = "") -> str:
        title = (title or "").strip()[:MAX_TITLE]
        content = (content or "").strip()
        if not title:
            return "[error] 缺少 title"
        if not content:
            return "[error] 缺少 content"
        if len(content) > MAX_CONTENT:
            content = content[:MAX_CONTENT] + "…（截断）"

        with _lock:
            self._ensure()
            existed = title in self._notes
            self._notes[title] = SharedNote(
                title=title, content=content, source=source, updated=_now()
            )
            # 超过上限就丢最旧的（按更新时间）
            if len(self._notes) > MAX_NOTES:
                oldest = sorted(self._notes.values(), key=lambda n: n.updated)[0]
                self._notes.pop(oldest.title, None)
            self._flush()
        return f"[ok] {'更新' if existed else '记下'}了共享笔记「{title}」（{len(content)} 字）"

    def read(self, title: str = "") -> str:
        with _lock:
            self._ensure()
            if not self._notes:
                return "[ok] 黑板是空的。"
            if title:
                note = self._notes.get(title.strip())
                if note is None:
                    names = "、".join(list(self._notes)[:8])
                    return f"[error] 没有「{title}」。现有：{names}"
                return self._render_one(note)
            ordered = sorted(self._notes.values(), key=lambda n: n.updated, reverse=True)
            return "\n\n".join(self._render_one(note) for note in ordered)

    def _render_one(self, note: SharedNote) -> str:
        source = f"（来自 {note.source}）" if note.source else ""
        return f"## {note.title}{source}\n{note.content}"

    def forget(self, title: str) -> str:
        with _lock:
            self._ensure()
            if self._notes.pop((title or "").strip(), None) is None:
                return f"[ok] 本来就没有「{title}」"
            self._flush()
        return f"[ok] 已删掉「{title}」"

    def clear(self) -> None:
        with _lock:
            self._notes = {}
            self._loaded = True
            self._flush()

    # ---------- 给 prompt 用 ----------

    def prompt_block(self, limit: int = 6, chars: int = 600) -> str:
        """拼进 system prompt 的一段。只放最近几条，别抢上下文。"""
        with _lock:
            self._ensure()
            if not self._notes:
                return ""
            ordered = sorted(self._notes.values(), key=lambda n: n.updated, reverse=True)
            lines = ["## 共享笔记（其它任务/更早的任务留下的结论）"]
            for note in ordered[:limit]:
                body = note.content.strip().replace("\n", " ")
                if len(body) > chars:
                    body = body[:chars] + "…"
                source = f"〔{note.source}〕" if note.source else ""
                lines.append(f"- {note.title}{source}：{body}")
            lines.append("需要全文用 share_read 查。")
            return "\n".join(lines)

    def count(self) -> int:
        with _lock:
            self._ensure()
            return len(self._notes)


_BOARD: SharedBoard | None = None
_source = threading.local()


def set_source(name: str) -> None:
    """谁在写（任务线程在 send 开头设一次）。"""
    _source.name = name


def current_source() -> str:
    return getattr(_source, "name", "")


def board_for(data_dir: Path | None = None) -> SharedBoard:
    """进程内共用一块黑板；第一次调用时绑定数据目录。"""
    global _BOARD
    if _BOARD is None:
        _BOARD = SharedBoard(data_dir)
    return _BOARD
