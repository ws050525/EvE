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
    # 校验：这些是实测踩过的坑，不能存进去
    if any("{" in str(step.get("text", "")) for step in steps) and not args.get("patterns"):
        return (
            "[error] 步骤里用了 {1} 之类的参数，但没给 patterns。"
            '请补上 patterns（例如 ["《(.+?)》"]），否则回放时参数会是空的。'
        )
    for step in steps:
        text = str(step.get("text") or "")
        target = str(step.get("target") or "")
        if step.get("do") == "type" and text in {"歌名", "内容", "文字", "X", "xxx"}:
            return (
                f"[error] 输入内容写成了占位词 {text!r}，回放时会真的输入这两个字。"
                "要么写具体值，要么用 {1} 并配上 patterns。"
            )
        if step.get("do") in {"click", "type"} and len(target) > 20:
            return (
                f"[error] target 太长，看着像描述而不是界面上的字：{target[:24]}…\n"
                "ui_find 是按**界面上真实显示的文字**找的，写「播放全部」这种短词；"
                "没有文字的控件用 click_point。"
            )

    store = _store(cfg)
    # 已经有一条看着像的，就别再存第二条——两条会互相抢，先来的那条还不一定是对的
    incoming = {str(k).strip() for k in (args.get("keywords") or [name]) if str(k).strip()}
    for existing in store.list_all():
        overlap = incoming & set(existing.keywords)
        if len(overlap) >= 2:
            return (
                f"[error] 已经有一条很像的记忆「{existing.name}」（共同触发词：{'、'.join(sorted(overlap))}）。\n"
                f"同一件事存两份会互相抢，而且先命中的那条可能是旧的。\n"
                f"要改就先用 macro_forget 删掉旧的再存，或者直接复用它。"
            )
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
