"""Agent 主循环：会话、工具调用、步数上限、结构化日志。

v0.4 起不再自己造 Provider，而是向 RuntimeManager 要：
档位决定 system prompt 和工具集，模型决定用哪份算力，两者解耦。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from eve_core.config import AppConfig, ModeConfig
from eve_core.provider import ChatResult, Provider
from eve_core.runtime import RuntimeManager

from .tools.registry import ToolRegistry, build_default_registry

MAX_STEPS = 6

ConfirmFn = Callable[[Any, dict[str, Any]], bool]


class Agent:
    def __init__(
        self,
        cfg: AppConfig,
        mode_name: str | None = None,
        runtime: RuntimeManager | None = None,
    ):
        self.cfg = cfg
        self.runtime = runtime or RuntimeManager(cfg)
        self.registry: ToolRegistry = build_default_registry()
        self.mode_name = mode_name or cfg.default_mode
        self.mode: ModeConfig
        self.messages: list[dict[str, Any]] = []
        self._model_override: str | None = None
        self._provider: Provider | None = None
        self._provider_model_id: str = ""
        self._activate(self.mode_name, reset=True)

    # ---------- 档位与模型 ----------

    @property
    def model_id(self) -> str:
        return self._model_override or self.mode.model

    @property
    def provider(self) -> Provider:
        """懒加载：只有真的要发请求时才拉起引擎。

        这样 /models、/scan、/key 这些命令不需要等模型加载。
        """
        model_id = self.model_id
        if self._provider is None or self._provider_model_id != model_id:
            self._provider = self.runtime.provider_for(model_id)
            self._provider_model_id = model_id
        return self._provider

    def _activate(self, name: str, reset: bool) -> None:
        mode = self.cfg.mode(name)
        system = mode.system_prompt.read_text(encoding="utf-8").strip()

        self.mode_name = name
        self.mode = mode
        self._provider = None  # 档位变了，下次用到再解析
        self._provider_model_id = ""
        self.active_tools = [t for t in mode.tools if self.registry.has(t)]
        missing = [t for t in mode.tools if not self.registry.has(t)]

        if reset or not self.messages:
            self.messages = [{"role": "system", "content": system}]
        else:
            self.messages[0] = {"role": "system", "content": system}

        self._warn = (
            f"档位 '{name}' 配置了尚未实现的工具：{', '.join(missing)}" if missing else ""
        )

    @property
    def warning(self) -> str:
        return getattr(self, "_warn", "")

    def switch_mode(self, name: str) -> str:
        """切档会重置上下文——不同档位的 system prompt 与工具集不同，混着聊没有意义。"""
        self._model_override = None
        self._activate(name, reset=True)
        return f"已切换到 {name} 档，模型 {self.model_id}"

    def switch_model(self, model_id: str) -> str:
        """只为当前档位换模型，保留对话上下文。"""
        model = self.cfg.model(model_id)
        previous = self._model_override
        self._model_override = model_id
        self._provider = None
        self._provider_model_id = ""
        try:
            self.provider  # 立即加载，好把失败尽早暴露给用户
        except Exception:
            self._model_override = previous  # 切失败就退回原模型
            raise
        status = self.runtime.status()
        detail = f"，加载 {status.load_seconds}s" if status.load_seconds else ""
        return f"当前档位已切到模型 {model_id}（{model.label}{detail}）"

    def reset(self) -> None:
        self._activate(self.mode_name, reset=True)

    # ---------- 主循环 ----------

    def send(self, text: str, confirm: ConfirmFn | None = None) -> str:
        self.messages.append({"role": "user", "content": text})
        schemas = self.registry.schemas(self.active_tools)

        for step in range(1, MAX_STEPS + 1):
            result = self.provider.chat(
                self.messages,
                schemas,
                max_tokens=self.mode.max_tokens,
                temperature=self.mode.temperature,
            )
            self._log(result, step, text)

            if not result.tool_calls:
                self.messages.append({"role": "assistant", "content": result.content})
                return result.content

            self.messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in result.tool_calls
                    ],
                }
            )

            for call in result.tool_calls:
                output = self.registry.dispatch(
                    call.name,
                    call.arguments,
                    allowed=set(self.active_tools),
                    confirm=confirm,
                    require_confirm=self.mode.require_confirm,
                )
                self._log_action(call.name, call.arguments, output)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": output,
                    }
                )

        return f"（已达 {MAX_STEPS} 步上限，停止。可以 /reset 后给更具体的指令）"

    # ---------- 日志 ----------

    def _log_action(self, name: str, args: dict[str, Any], output: str) -> None:
        """动手的事单独记一份，便于回放追责。"""
        try:
            self.cfg.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.cfg.log_dir / f"actions-{datetime.now():%Y-%m-%d}.jsonl"
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.mode_name,
                "model": self.model_id,
                "tool": name,
                "args": args,
                "result": output[:300],
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _log(self, result: ChatResult, step: int, user_text: str) -> None:
        try:
            self.cfg.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.cfg.log_dir / f"{datetime.now():%Y-%m-%d}.jsonl"
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.mode_name,
                "model": result.model or self.model_id,
                "step": step,
                "latency_ms": result.latency_ms,
                "usage": result.usage,
                "user": user_text[:500],
                "reply": (result.content or "")[:500],
                "tool_calls": [c.name for c in result.tool_calls],
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 日志失败不影响对话
