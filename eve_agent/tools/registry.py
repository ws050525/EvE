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
        auto_ok: bool = False,
    ) -> str:
        """执行工具，永远返回字符串——失败也返回文本，让模型自己看见并纠正。

        `auto_ok=True` 是"最高权限"开关：连高风险工具都不再询问。
        """
        if name not in allowed:
            return f"[error] 工具 '{name}' 不在当前档位的白名单里"
        spec = self._tools.get(name)
        if spec is None:
            return f"[error] 不存在工具 '{name}'"

        if "__raw__" in args:
            # 参数不是合法 JSON。实测最常见的两个原因：输出被 max_tokens 截断，
            # 或者字符串里塞了没转义的换行。不给提示的话，模型会一模一样地重试十次。
            preview = str(args["__raw__"])[:120].replace("\n", "\\n")
            return (
                "[error] 工具参数不是合法 JSON，多半是内容太长被 max_tokens 截断了。\n"
                f"  收到的片段：{preview}…\n"
                "  改法：一次少写一点，分几次调用（比如先把文件头写进去，再追加剩余部分）。"
            )

        # 低风险工具（读窗口、读网页）不打断人；中高风险才确认
        needs_ok = (
            not auto_ok
            and (spec.risk == "high" or (require_confirm and spec.risk != "low"))
        )
        if needs_ok:
            if confirm is None:
                return f"[denied] 工具 '{name}' 需要用户确认，但当前是非交互模式"
            if not confirm(spec, args):
                return f"[denied] 用户拒绝了 '{name}'"

        try:
            return spec.handler(args)
        except Exception as exc:  # 工具内部错误不能炸掉整个会话
            return f"[error] {type(exc).__name__}: {exc}"


def build_default_registry(cfg=None) -> ToolRegistry:
    from .builtin import register_builtin
    from .desktop import register_desktop
    from .office import register_office
    from .shell import register_shell
    from .web import register_web
    from .workspace import register_workspace

    registry = ToolRegistry()
    register_builtin(registry)
    register_desktop(registry)
    register_web(registry, cfg)
    register_workspace(registry, cfg)
    register_office(registry, cfg)
    register_shell(registry, cfg)
    return registry
