"""工具注册表。

一个工具 = 名字 + 给模型看的 JSON Schema + 真正执行的函数 + 风险等级。
风险等级决定要不要向用户确认；`require_confirm` 在档位上是总开关。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], str]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    risk: str = "low"  # low | medium | high


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册：{spec.name}")
        self._tools[spec.name] = spec

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def spec(self, name: str) -> ToolSpec:
        return self._tools[name]

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        """把 ToolSpec 转成 OpenAI function-calling 的 tools 数组。"""
        out = []
        for name in names:
            spec = self._tools.get(name)
            if spec is None:
                continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
            )
        return out

    def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        allowed: set[str],
        confirm: Callable[[ToolSpec, dict[str, Any]], bool] | None = None,
        require_confirm: bool = False,
    ) -> str:
        """执行工具，永远返回字符串——失败也返回文本，让模型自己看见并纠正。"""
        if name not in allowed:
            return f"[error] 工具 '{name}' 不在当前档位的白名单里"
        spec = self._tools.get(name)
        if spec is None:
            return f"[error] 不存在工具 '{name}'"

        needs_ok = require_confirm or spec.risk == "high"
        if needs_ok:
            if confirm is None:
                return f"[denied] 工具 '{name}' 需要用户确认，但当前是非交互模式"
            if not confirm(spec, args):
                return f"[denied] 用户拒绝了 '{name}'"

        try:
            return spec.handler(args)
        except Exception as exc:  # 工具内部错误不能炸掉整个会话
            return f"[error] {type(exc).__name__}: {exc}"


def build_default_registry() -> ToolRegistry:
    from .builtin import register_builtin
    from .desktop import register_desktop

    registry = ToolRegistry()
    register_builtin(registry)
    register_desktop(registry)
    return registry
