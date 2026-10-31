"""Agent 主循环：会话、工具调用、步数上限、结构化日志。

v0.4 起不再自己造 Provider，而是向 RuntimeManager 要：
档位决定 system prompt 和工具集，模型决定用哪份算力，两者解耦。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

from eve_core.config import AppConfig, ModeConfig
from eve_core.events import EventBus
from eve_core.provider import ChatResult, Provider
from eve_core.runtime import RuntimeManager

from .persona import Persona
from .tools.registry import ToolRegistry, build_default_registry

MAX_STEPS = 6

ConfirmFn = Callable[[Any, dict[str, Any]], bool]


class Agent:
    def __init__(
        self,
        cfg: AppConfig,
        mode_name: str | None = None,
        runtime: RuntimeManager | None = None,
        bus: EventBus | None = None,
    ):
        self.cfg = cfg
        self.bus = bus or EventBus()
        self.runtime = runtime or RuntimeManager(cfg, on_event=self._emit_runtime)
        self.registry: ToolRegistry = build_default_registry()
        self.persona = Persona()
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

    # ---------- 事件 ----------

    def _emit_runtime(self, kind: str, data: dict[str, Any]) -> None:
        """把核心层的事件转发到自己的总线。"""
        self.bus.emit(kind, **data)

    def _outgoing(self) -> list[dict[str, Any]]:
        """把人格状态临时拼进 system prompt，不改动会话本身。"""
        extra = self.persona.prompt_extra()
        if not extra:
            return self.messages
        out = list(self.messages)
        if out and out[0].get("role") == "system":
            out[0] = {"role": "system", "content": out[0]["content"] + "\n\n" + extra}
        return out

    # ---------- 流式 ----------

    def _stream_once(self, user_text: str, step: int) -> str | None:
        """流式跑一轮；一个 token 都没吐时返回 None，让调用方回退到普通请求。"""
        started = time.perf_counter()
        parts: list[str] = []
        try:
            for delta in self.provider.stream(
                self._outgoing(),
                None,
                max_tokens=self.mode.max_tokens,
                temperature=self.mode.temperature,
            ):
                parts.append(delta)
                self.bus.emit("reply_chunk", text=delta)
        except Exception as exc:
            self.bus.emit("error", message=str(exc), step=step)
            raise

        if not parts:
            return None

        raw = "".join(parts)
        latency_ms = int((time.perf_counter() - started) * 1000)
        self.messages.append({"role": "assistant", "content": raw})
        final = self.persona.decorate(raw, mode=self.mode_name)
        self._log(
            ChatResult(content=raw, model=self.model_id, latency_ms=latency_ms), step, user_text
        )
        self.bus.emit(
            "reply_done",
            text=final,
            latency_ms=latency_ms,
            usage={},
            model=self.model_id,
        )
        return final

    # ---------- 主循环 ----------

    def send(self, text: str, confirm: ConfirmFn | None = None) -> str:
        self.persona.observe_user(text)
        self.bus.emit("user_message", text=text, protest_level=self.persona.protest_level)
        self.messages.append({"role": "user", "content": text})

        # 先确保模型可用：这样事件顺序是 engine_starting -> engine_ready -> thinking
        try:
            _ = self.provider
        except Exception as exc:
            self.bus.emit("error", message=str(exc), step=0)
            raise

        # 罢工时不给工具：模型只能说话，不能动手
        schemas = self.registry.schemas(self.active_tools) if self.persona.allow_tools else []

        for step in range(1, MAX_STEPS + 1):
            self.bus.emit("thinking", step=step, model=self.model_id)

            # 没有工具时才走流式：工具调用不适合在 MVP 里分片拼装
            if not schemas:
                streamed = self._stream_once(text, step)
                if streamed is not None:
                    return streamed

            try:
                result = self.provider.chat(
                    self._outgoing(),
                    schemas,
                    max_tokens=self.mode.max_tokens,
                    temperature=self.mode.temperature,
                )
            except Exception as exc:
                self.bus.emit("error", message=str(exc), step=step)
                raise
            self._log(result, step, text)

            if not result.tool_calls:
                self.messages.append({"role": "assistant", "content": result.content})
                final = self.persona.decorate(result.content, mode=self.mode_name)
                self.bus.emit(
                    "reply_done",
                    text=final,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    model=result.model or self.model_id,
                )
                return final

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
                self.bus.emit("tool_start", name=call.name, args=call.arguments)
                output = self.registry.dispatch(
                    call.name,
                    call.arguments,
                    allowed=set(self.active_tools),
                    confirm=confirm,
                    require_confirm=self.mode.require_confirm,
                )
                ok = not output.startswith(("[error]", "[denied]"))
                self.bus.emit("tool_end", name=call.name, ok=ok, result=output[:200])
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
