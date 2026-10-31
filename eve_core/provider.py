"""Provider 层：整个项目唯一知道"某个厂商"存在的地方。

上层只发 `ChatRequest`、收 `ChatResult`。换模型 = 改配置，不改业务代码。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import ConfigError, ProviderConfig


class ProviderError(RuntimeError):
    """调用模型失败，消息面向用户。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    name: str
    model: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> ChatResult: ...


class OpenAICompatProvider:
    """任何说 OpenAI 兼容协议的服务都能用：llama.cpp / vLLM / Ollama / DeepSeek / OpenAI…"""

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.name = cfg.name
        self.model = cfg.model
        self._url = cfg.base_url.rstrip("/") + "/chat/completions"
        self._key = cfg.resolved_key()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self._key}",
            },
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise ProviderError(f"{self.name}: HTTP {exc.code} — {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"{self.name}: 连不上 {self.cfg.base_url}（{exc.reason}）。"
                f"本地档请先运行 python -m eve.serve"
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(f"{self.name}: 请求超时（{self.cfg.timeout}s）") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for idx, item in enumerate(message.get("tool_calls") or []):
            fn = item.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {"__raw__": raw_args}
            else:
                args = raw_args
            calls.append(
                ToolCall(
                    id=item.get("id") or f"call_{idx}",
                    name=fn.get("name", ""),
                    arguments=args,
                )
            )

        return ChatResult(
            content=message.get("content") or "",
            tool_calls=calls,
            model=data.get("model", self.model),
            usage=data.get("usage") or {},
            latency_ms=latency_ms,
            raw=data,
        )


def build_provider(cfg: ProviderConfig) -> Provider:
    if cfg.type == "openai_compatible":
        return OpenAICompatProvider(cfg)
    raise ConfigError(f"不支持的 provider type: {cfg.type}")
