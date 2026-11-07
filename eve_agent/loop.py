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
from eve_core.pricing import ModelPrice, price_for
from eve_core.provider import ChatResult, Provider
from eve_core.runtime import RuntimeManager
from eve_core.usage import UsageTracker

from .context import (
    SUMMARY_SYSTEM,
    ContextPolicy,
    compact,
    messages_tokens,
)
from .persona import Persona
from .tools.registry import ToolRegistry, build_default_registry

MAX_STEPS_DEFAULT = 12  # 只有配置里拿不到时才用
MIN_STEPS = 1
MAX_STEPS_CAP = 60

# 自动预算的天花板：云端窗口再大也别一上来就塞满，那是真金白银
AUTO_BUDGET_CAP = 24_000
AUTO_BUDGET_FLOOR = 1024

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
        self.registry: ToolRegistry = build_default_registry(cfg)
        self.persona = Persona()
        self.usage = UsageTracker()
        self.mode_name = mode_name or cfg.default_mode
        self.mode: ModeConfig
        self.messages: list[dict[str, Any]] = []
        self._summary: str = ""  # 已经被压掉的早先轮次的摘要
        self._context_limit: int = 0  # 运行时覆盖：0 = 跟随配置/自动
        self._max_steps: int = getattr(cfg, "max_steps", MAX_STEPS_DEFAULT)
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
            self._summary = ""
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
        return f"已切换到{self.mode.display}档，模型 {self.model_id}"

    @property
    def mode_label(self) -> str:
        return self.mode.display

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

    def forget_provider(self) -> None:
        """丢掉缓存的 provider，下次请求重新解析（改 Key 之后要调它）。"""
        self._provider = None
        self._provider_model_id = ""

    # ---------- 事件 ----------

    def _emit_runtime(self, kind: str, data: dict[str, Any]) -> None:
        """把核心层的事件转发到自己的总线。"""
        self.bus.emit(kind, **data)

    def _outgoing(self) -> list[dict[str, Any]]:
        """把人格状态和历史摘要临时拼进 system prompt，不改动会话本身。"""
        extra = self.persona.prompt_extra()
        summary = getattr(self, "_summary", "")
        head = "\n\n".join(
            part for part in (extra, f"## 先前对话摘要\n{summary}" if summary else "") if part
        )
        out = list(self.messages)
        if head and out and out[0].get("role") == "system":
            out[0] = {"role": "system", "content": out[0]["content"] + "\n\n" + head}
        return out

    # ---------- 上下文预算 ----------

    @property
    def context_policy(self) -> ContextPolicy:
        """prompt 预算：运行时覆盖 > 档位 > 全局 > 按窗口自动，最后按真实窗口兜底。"""
        model = self.cfg.model(self.model_id)
        ctx = model.ctx or 8192
        ceiling = max(512, ctx - self.mode.max_tokens - 128)

        budget = self._context_limit or self.mode.context_tokens or self.cfg.context_tokens
        if budget <= 0:  # 自动：窗口减去输出和一点余量，再封顶
            budget = ctx - self.mode.max_tokens - 512
            budget = min(AUTO_BUDGET_CAP, max(AUTO_BUDGET_FLOOR, budget))
        budget = max(AUTO_BUDGET_FLOOR, budget)

        return ContextPolicy(
            max_prompt_tokens=min(budget, ceiling),
            keep_recent_turns=max(1, self.cfg.keep_recent_turns),
            summary_max_tokens=min(800, max(200, budget // 6)),
        )

    @property
    def context_limit(self) -> int:
        """当前生效的预算（0 表示"自动"这个设定本身，不是 0 token）。"""
        return self.context_policy.max_prompt_tokens

    @property
    def context_setting(self) -> int:
        """用户设定的预算：0 = 自动。跟 context_limit（生效值）不是一回事。"""
        return self._context_limit

    @property
    def max_steps(self) -> int:
        return self._max_steps

    def set_context_limit(self, tokens: int) -> str:
        """运行时改预算；传 0 回到"自动"。立刻生效，下一轮就按新预算裁。"""
        tokens = int(tokens)
        if tokens < 0:
            tokens = 0
        if tokens > AUTO_BUDGET_CAP:
            tokens = AUTO_BUDGET_CAP
        self._context_limit = tokens
        budget = self.context_policy.max_prompt_tokens
        label = "自动" if tokens == 0 else f"{tokens} tokens"
        return f"上下文预算已设为 {label}（当前生效 {budget} tokens）"

    def set_max_steps(self, steps: int) -> str:
        steps = max(MIN_STEPS, min(MAX_STEPS_CAP, int(steps)))
        self._max_steps = steps
        return f"步数上限已设为 {steps} 步"

    @property
    def context_usage(self) -> tuple[int, int]:
        """(当前估算 token, 预算)；给 /status 与界面用。"""
        return messages_tokens(self._outgoing()), self.context_policy.max_prompt_tokens

    @property
    def summary(self) -> str:
        """已经被压掉的早先轮次的摘要（空串表示还没压过）。"""
        return self._summary

    def _enforce_context(self) -> None:
        """请求前压一次上下文。没超预算就什么都不做，所以可以每步都调。"""
        policy = self.context_policy
        before, _ = self.context_usage
        if before <= policy.max_prompt_tokens:
            return

        self.bus.emit(
            "context_compacting", tokens=before, budget=policy.max_prompt_tokens
        )
        self.messages, self._summary, report = compact(
            self.messages, policy, summary=self._summary, summarize=self._summarize
        )
        self.bus.emit(
            "context_compacted",
            dropped_turns=report.dropped_turns,
            summarized=report.summarized,
            truncated_chars=report.truncated_chars,
            tokens_before=report.tokens_before,
            tokens_after=report.tokens_after,
        )

    def _summarize(self, text: str, max_tokens: int) -> str:
        """让当前模型把老对话压成摘要。失败会抛出去，由 compact 兜底。"""
        result = self.provider.chat(
            [
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": text},
            ],
            None,
            max_tokens=max_tokens,
            temperature=0.2,
        )
        # 摘要也是一次真实调用，同样要记账——不然云端账单会"看不见"这一块
        self._record_usage(result.usage, result.latency_ms, kind="summary")
        return (result.content or "").strip()

    # ---------- 用量与花费 ----------

    @property
    def price(self) -> ModelPrice:
        return price_for(self.cfg.model(self.model_id))

    def _record_usage(
        self, usage: dict[str, Any] | None, latency_ms: int = 0, kind: str = "chat"
    ):
        price = self.price
        call = self.usage.record(self.model_id, usage, price, latency_ms, kind)
        totals = self.usage.totals()
        self.bus.emit(
            "usage",
            model=self.model_id,
            call_kind=kind,
            prompt_tokens=call.usage.prompt,
            completion_tokens=call.usage.completion,
            cached_tokens=call.usage.cached,
            cost=call.cost,
            free=price.free,
            currency=price.currency,
            session_cost=totals.cost,
            session_prompt=totals.usage.prompt,
            session_completion=totals.usage.completion,
        )
        return call

    def usage_summary(self) -> dict[str, Any]:
        """会话账本，给 /cost 与状态栏用。累计跨档位保留。"""
        totals = self.usage.totals()
        return {
            "cost": totals.cost,
            "calls": totals.calls,
            "prompt": totals.usage.prompt,
            "completion": totals.usage.completion,
            "cached": totals.usage.cached,
            "per_model": self.usage.per_model(),
        }

    # ---------- 流式 ----------

    def _stream_once(self, user_text: str, step: int) -> str | None:
        """流式跑一轮；一个 token 都没吐时返回 None，让调用方回退到普通请求。"""
        started = time.perf_counter()
        parts: list[str] = []
        captured: dict[str, Any] = {}  # 云端会在最后一个 SSE 块里给 usage
        try:
            for delta in self.provider.stream(
                self._outgoing(),
                None,
                max_tokens=self.mode.max_tokens,
                temperature=self.mode.temperature,
                on_usage=captured.update,
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
        call = self._record_usage(captured, latency_ms)
        self.bus.emit(
            "reply_done",
            text=final,
            latency_ms=latency_ms,
            usage=captured,
            cost=call.cost,
            session_cost=self.usage.session_cost,
            free=self.price.free,
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

        for step in range(1, self._max_steps + 1):
            # 每步都量一次：工具结果可能刚把窗口顶爆，必须在发请求前压回去
            self._enforce_context()
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
            call = self._record_usage(result.usage, result.latency_ms)

            if result.finish_reason == "length":
                # 输出被 max_tokens 砍了：工具参数会变成非法 JSON，正文也会是半句
                self.bus.emit("truncated", step=step, model=self.model_id)

            if not result.tool_calls:
                self.messages.append({"role": "assistant", "content": result.content})
                final = self.persona.decorate(result.content, mode=self.mode_name)
                self.bus.emit(
                    "reply_done",
                    text=final,
                    latency_ms=result.latency_ms,
                    usage=result.usage,
                    cost=call.cost,
                    session_cost=self.usage.session_cost,
                    free=self.price.free,
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
                    auto_ok=getattr(self.cfg, "auto_confirm", False),
                )
                ok = not output.startswith(("[error]", "[denied]"))
                self.bus.emit("tool_end", name=call.name, ok=ok, result=output[:200])
                self._log_action(call.name, call.arguments, output)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": self._cap_tool_output(output),
                    }
                )

        return f"（已达 {self._max_steps} 步上限，停止。可以 /reset 后给更具体的指令，或调高步数上限）"

    # ---------- 日志 ----------

    def _cap_tool_output(self, output: str) -> str:
        """一条工具结果最多留多少字符。

        网页正文、目录列表这类东西动辄上万字，原样塞进历史会直接吃掉整个窗口，
        之后每步都要靠截断来救。从入库处就卡住，比事后补救便宜得多。
        """
        limit = max(500, self.cfg.tool_output_chars)
        if len(output) <= limit:
            return output
        dropped = len(output) - limit
        return f"{output[:limit]}\n…（工具输出过长，已省略 {dropped} 字符）"

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
