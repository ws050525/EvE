"""灵魂文件的工具：读、记、忘。

`Soul` 负责读写文件，这里只包一层给模型用的接口。
"""

from __future__ import annotations

from typing import Any

from ..soul import Soul
from .registry import ToolRegistry, ToolSpec


def _soul(cfg) -> Soul:
    return Soul(cfg)


def _soul_read(args: dict[str, Any], cfg) -> str:
    soul = _soul(cfg)
    body = soul.text()
    learned = soul.learned()
    if not body and not learned:
        return (
            "[ok] 灵魂文件是空的。"
            f"路径：{soul.path}（写点长期偏好进去，以后简写指令才能补全）"
        )
    parts = [f"[ok] 灵魂文件：{soul.path}"]
    if body:
        parts.append(body[:1500])
    if learned:
        parts.append("## 我记下的")
        parts.extend(f"- {fact}" for fact in learned[-10:])
    return "\n".join(parts)


def _soul_remember(args: dict[str, Any], cfg) -> str:
    fact = str(args.get("fact") or "").strip()
    ok, message = _soul(cfg).remember(fact)
    return ("[ok] " if ok else "[error] ") + message


def _soul_forget(args: dict[str, Any], cfg) -> str:
    keyword = str(args.get("keyword") or "").strip()
    removed = _soul(cfg).forget(keyword)
    if not removed:
        return f"[error] 没有包含「{keyword}」的记忆"
    return f"[ok] 删掉了 {removed} 条"


def register_soul(registry: ToolRegistry, cfg=None) -> None:
    registry.register(
        ToolSpec(
            name="soul_read",
            description="【灵魂】读一遍长期偏好和简写约定（用户手写的那份 + 你自己攒的）",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _soul_read(args, cfg),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="soul_remember",
            description=(
                "【灵魂】把一条**长期有效**的偏好记下来（不是这一次的临时信息）。"
                "比如「他说『搜下』指的是网易云搜歌并播放」「他不喜欢反复确认」。"
                "一次只记一条，写清楚触发场景。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "一句话，比如「说『发我』时指发到微信文件传输助手」"}
                },
                "required": ["fact"],
            },
            handler=lambda args: _soul_remember(args, cfg),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="soul_forget",
            description="【灵魂】删掉记错/过时的偏好",
            parameters={
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "包含这个词的条目会被删掉"}},
                "required": ["keyword"],
            },
            handler=lambda args: _soul_forget(args, cfg),
            risk="low",
        )
    )
