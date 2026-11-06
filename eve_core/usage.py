"""会话级用量账本：谁调了几次、烧了多少 token、折合多少钱。

只记账，不做展示——CLI 和界面各自决定怎么显示。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .pricing import ModelPrice, TokenUsage, parse_usage


@dataclass
class CallRecord:
    ts: str
    model_id: str
    kind: str  # chat | summary
    usage: TokenUsage
    cost: float
    latency_ms: int


@dataclass
class ModelTotal:
    calls: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost: float = 0.0


class UsageTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.records: list[CallRecord] = []
        self._by_model: dict[str, ModelTotal] = {}

    def record(
        self,
        model_id: str,
        usage: dict[str, Any] | TokenUsage | None,
        price: ModelPrice,
        latency_ms: int = 0,
        kind: str = "chat",
    ) -> CallRecord:
        parsed = usage if isinstance(usage, TokenUsage) else parse_usage(usage)
        cost = price.cost(parsed)
        call = CallRecord(
            ts=datetime.now().isoformat(timespec="seconds"),
            model_id=model_id,
            kind=kind,
            usage=parsed,
            cost=cost,
            latency_ms=latency_ms,
        )
        with self._lock:
            self.records.append(call)
            total = self._by_model.setdefault(model_id, ModelTotal())
            total.calls += 1
            total.usage = TokenUsage(
                prompt=total.usage.prompt + parsed.prompt,
                completion=total.usage.completion + parsed.completion,
                cached=total.usage.cached + parsed.cached,
            )
            total.cost += cost
        return call

    # ---------- 汇总 ----------

    def totals(self) -> ModelTotal:
        with self._lock:
            prompt = sum(t.usage.prompt for t in self._by_model.values())
            completion = sum(t.usage.completion for t in self._by_model.values())
            cached = sum(t.usage.cached for t in self._by_model.values())
            calls = sum(t.calls for t in self._by_model.values())
            cost = sum(t.cost for t in self._by_model.values())
        return ModelTotal(
            calls=calls,
            usage=TokenUsage(prompt=prompt, completion=completion, cached=cached),
            cost=cost,
        )

    def per_model(self) -> dict[str, ModelTotal]:
        with self._lock:
            return dict(self._by_model)

    @property
    def session_cost(self) -> float:
        return self.totals().cost

    @property
    def last(self) -> CallRecord | None:
        with self._lock:
            return self.records[-1] if self.records else None

    def reset(self) -> None:
        with self._lock:
            self.records.clear()
            self._by_model.clear()
