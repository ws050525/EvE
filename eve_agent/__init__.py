"""EVE 的 agent 层：主循环、人格状态机、工具。

对外只暴露 Agent；工具注册表在 .tools 里，按风险等级分层。
"""

from .loop import Agent

__all__ = ["Agent"]
