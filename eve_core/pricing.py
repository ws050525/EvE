"""计费：把 usage 里的 token 数换算成钱。

这里只做纯计算，不关心是谁调的、调了几次。累计交给 `eve_core.usage.UsageTracker`。

价格来源：`[models.*]` 里的 `price_in` / `price_out` / `price_cached_in`，
单位是「元 / 百万 tokens」。本地模型填 0，就是不计费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PER_MTOK = 1_000_000


@dataclass(frozen=True)
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    cached: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def __bool__(self) -> bool:
        return bool(self.prompt or self.completion)


@dataclass(frozen=True)
class ModelPrice:
    input: float = 0.0  # 元 / 百万 tokens（未命中缓存）
    output: float = 0.0
    cached_input: float = 0.0  # 元 / 百万 tokens（命中缓存）
    currency: str = "CNY"

    @property
    def free(self) -> bool:
        return not (self.input or self.output or self.cached_input)

    @property
    def cached_rate(self) -> float:
        # 没配缓存价就按普通输入价算：宁可高估，也别假装便宜
        return self.cached_input if self.cached_input > 0 else self.input

    def cost(self, usage: TokenUsage) -> float:
        if self.free or not usage:
            return 0.0
        cached = max(0, min(usage.cached, usage.prompt))
        uncached = max(0, usage.prompt - cached)
        total = (
            uncached * self.input
            + cached * self.cached_rate
            + usage.completion * self.output
        )
        return total / PER_MTOK


FREE = ModelPrice()


def price_for(model: Any) -> ModelPrice:
    """从 ModelConfig 取价格。拿不到就当作免费。"""
    if model is None:
        return FREE
    return ModelPrice(
        input=float(getattr(model, "price_in", 0.0) or 0.0),
        output=float(getattr(model, "price_out", 0.0) or 0.0),
        cached_input=float(getattr(model, "price_cached_in", 0.0) or 0.0),
    )


def parse_usage(raw: dict[str, Any] | None) -> TokenUsage:
    """不同厂商的 usage 字段不完全一样，这里统一成 TokenUsage。

    - OpenAI 风格：prompt_tokens / completion_tokens，命中缓存放在
      prompt_tokens_details.cached_tokens
    - DeepSeek：额外给 prompt_cache_hit_tokens
    """
    raw = raw or {}
    prompt = _as_int(raw.get("prompt_tokens"))
    completion = _as_int(raw.get("completion_tokens"))

    cached = _as_int(raw.get("prompt_cache_hit_tokens"))
    if not cached:
        details = raw.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cached = _as_int(details.get("cached_tokens"))

    if not prompt and not completion:
        # 有些服务只给 total_tokens
        total = _as_int(raw.get("total_tokens"))
        if total:
            completion = 0
            prompt = total

    return TokenUsage(prompt=prompt, completion=completion, cached=cached)


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def format_money(value: float, currency: str = "CNY") -> str:
    symbol = "¥" if currency == "CNY" else "$"
    if value <= 0:
        return f"{symbol}0"
    if value < 0.0001:
        return f"{symbol}<0.0001"
    if value < 0.01:
        return f"{symbol}{value:.4f}"
    if value < 1:
        return f"{symbol}{value:.3f}"
    return f"{symbol}{value:.2f}"
