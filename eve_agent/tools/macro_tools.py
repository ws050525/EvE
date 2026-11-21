"""语义动作记忆的工具：让 agent 自己把做过的操作存下来，下次直接回放。"""

from __future__ import annotations

from typing import Any

from ..macros import MacroStore
from .registry import ToolRegistry, ToolSpec

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "do": {
            "type": "string",
            "enum": ["launch", "focus", "click", "type", "key", "wait"],
            "description": "动作类型",
        },
        "app": {"type": "string", "description": "do=launch 时：程序名"},
        "window": {"type": "string", "description": "do=focus 时：窗口标题"},
        "target": {"type": "string", "description": "do=click/type 时：控件上的文字"},
        "text": {"type": "string", "description": "do=type 时：输入内容"},
        "keys": {"type": "string", "description": "do=key 时：组合键，如 ctrl+f"},
    },
    "required": ["do"],
}


def _store(cfg) -> MacroStore:
    root = getattr(cfg, "data_dir", None) or ".data"
    return MacroStore(root)


def _macro_save(args: dict[str, Any], cfg) -> str:
    name = str(args.get("name") or "").strip()
    if not name:
        return "[error] 缺少 name（给这套操作起个名，例如「网易云搜歌播放」）"
    steps = args.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return "[error] steps 要写成动作列表，例如 [{\"do\":\"click\",\"target\":\"播放全部\"}]"
    keywords = args.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k for k in keywords.replace("，", ",").split(",") if k.strip()]
    if not keywords:
        keywords = [name]
    store = _store(cfg)
    macro = store.add(
        name=name,
        keywords=[str(k).strip() for k in keywords if str(k).strip()],
        steps=[step for step in steps if isinstance(step, dict) and step.get("do")],
        verify=str(args.get("verify") or ""),
    )
    chain = " → ".join(step.describe() for step in macro.steps)
    return (
        f"[ok] 记住了「{macro.name}」（{len(macro.steps)} 步）：{chain}\n"
        f"触发词：{'、'.join(macro.keywords)}。\n"
        "下次说到这些词就会直接回放，不再重新想一遍。"
    )


def _macro_list(args: dict[str, Any], cfg) -> str:
    store = _store(cfg)
    macros = store.list_all()
    if not macros:
        return "[ok] 还没有记录任何操作"
    lines = [f"[ok] 记着 {len(macros)} 套操作："]
    for macro in macros:
        lines.append(f"  · {macro.name}（{len(macro.steps)} 步，用过 {macro.hits} 次）")
        lines.append(f"    {macro.summary()}")
    return "\n".join(lines)


def _macro_forget(args: dict[str, Any], cfg) -> str:
    key = str(args.get("name") or args.get("id") or "").strip()
    if not key:
        return "[error] 缺少 name"
    store = _store(cfg)
    if not store.forget(key):
        return f"[error] 没有叫「{key}」的记忆"
    return f"[ok] 已经忘掉「{key}」"


def register_macros(registry: ToolRegistry, cfg=None) -> None:
    registry.register(
        ToolSpec(
            name="macro_save",
            description=(
                "【记操作】把刚做完的一套桌面操作记成「语义动作」，以后同类任务直接回放，省掉几十步。"
                "只记**意图**不记坐标：点哪个字、输入什么、按什么键。"
                "在任务**成功之后**调用，一次性把整条链路写清楚。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "这套操作的名字，例如「网易云搜歌播放」"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "触发词：用户这么说时回放，例如 [\"网易云\", \"搜歌\", \"播放\"]",
                    },
                    "steps": {"type": "array", "items": STEP_SCHEMA, "description": "动作列表，按顺序"},
                    "verify": {"type": "string", "description": "回放完怎么确认成功，一句话"},
                },
                "required": ["name", "steps"],
            },
            handler=lambda args: _macro_save(args, cfg),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="macro_list",
            description="【记忆】看看记过哪些操作套路",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: _macro_list(args, cfg),
            risk="low",
        )
    )
    registry.register(
        ToolSpec(
            name="macro_forget",
            description="【记忆】忘掉一套记错/过时的操作",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "名字或 id"}},
                "required": ["name"],
            },
            handler=lambda args: _macro_forget(args, cfg),
            risk="low",
        )
    )
