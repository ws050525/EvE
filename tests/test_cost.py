"""计费与用量账本的单元测试。纯计算，不需要模型。

跑法： & 'D:\\python\\python.exe' tests\\test_cost.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eve_core.config import ModelConfig
from eve_core.pricing import (
    ModelPrice,
    TokenUsage,
    format_money,
    parse_usage,
    price_for,
)
from eve_core.usage import UsageTracker

CHECKS = []


def case(fn):
    CHECKS.append(fn)
    return fn


@case
def parse_openai_style_usage():
    usage = parse_usage(
        {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
    )
    assert usage.prompt == 1200, usage
    assert usage.completion == 300
    assert usage.cached == 800
    assert usage.total == 1500


@case
def parse_deepseek_style_usage():
    usage = parse_usage(
        {
            "prompt_tokens": 900,
            "completion_tokens": 120,
            "prompt_cache_hit_tokens": 640,
            "prompt_cache_miss_tokens": 260,
        }
    )
    assert usage.cached == 640, usage


@case
def parse_missing_or_weird_usage():
    assert not parse_usage(None)
    assert not parse_usage({})
    # 只给 total 的退化情形别把 prompt 算成 0
    usage = parse_usage({"total_tokens": 42})
    assert usage.total == 42, usage
    # 脏数据不能炸
    assert parse_usage({"prompt_tokens": "abc"}).prompt == 0


@case
def cost_counts_cache_at_discount():
    price = ModelPrice(input=2.0, output=8.0, cached_input=0.2)
    usage = TokenUsage(prompt=1_000_000, completion=0, cached=0)
    assert abs(price.cost(usage) - 2.0) < 1e-9, price.cost(usage)

    cached = TokenUsage(prompt=1_000_000, completion=0, cached=1_000_000)
    assert abs(price.cost(cached) - 0.2) < 1e-9, price.cost(cached)


@case
def cost_splits_cached_and_uncached():
    price = ModelPrice(input=9.0, output=27.0, cached_input=0.9)
    usage = TokenUsage(prompt=1_000_000, completion=1_000_000, cached=400_000)
    # 600k 普通输入 + 400k 缓存输入 + 1M 输出
    expected = 0.6 * 9.0 + 0.4 * 0.9 + 27.0
    assert abs(price.cost(usage) - expected) < 1e-9, price.cost(usage)


@case
def cached_rate_falls_back_to_full_price():
    price = ModelPrice(input=5.0, output=10.0, cached_input=0.0)
    assert price.cached_rate == 5.0, "没配缓存价就该按全价算，不能假装免费"


@case
def free_model_costs_nothing():
    price = ModelPrice()
    assert price.free
    assert price.cost(TokenUsage(prompt=1_000_000, completion=1_000_000)) == 0.0


@case
def price_for_reads_model_config():
    model = ModelConfig(
        id="deepseek-pro", label="Pro", base_url="x", price_in=9.0, price_out=27.0,
        price_cached_in=0.9,
    )
    price = price_for(model)
    assert price.input == 9.0 and price.output == 27.0
    assert not price.free

    local = ModelConfig(id="local", label="本地", path="m.gguf")
    assert price_for(local).free


@case
def money_formatting_is_readable():
    assert format_money(0) == "¥0"
    assert format_money(0.00001) == "¥<0.0001"
    assert format_money(0.0042) == "¥0.0042"
    assert format_money(0.123) == "¥0.123"
    assert format_money(12.345) == "¥12.35"


@case
def tracker_accumulates_and_splits_by_model():
    tracker = UsageTracker()
    cloud = ModelPrice(input=2.0, output=8.0, cached_input=0.2)
    local = ModelPrice()

    tracker.record("deepseek-flash", {"prompt_tokens": 1000, "completion_tokens": 200}, cloud)
    tracker.record("deepseek-flash", {"prompt_tokens": 500, "completion_tokens": 100}, cloud)
    tracker.record("qwen3vl-4b", {"prompt_tokens": 300, "completion_tokens": 300}, local)

    totals = tracker.totals()
    assert totals.calls == 3, totals
    assert totals.usage.prompt == 1800, totals
    assert totals.usage.completion == 600, totals

    expected = (1500 * 2.0 + 300 * 8.0) / 1_000_000
    assert abs(totals.cost - expected) < 1e-9, totals.cost

    per = tracker.per_model()
    assert set(per) == {"deepseek-flash", "qwen3vl-4b"}, per
    assert per["qwen3vl-4b"].cost == 0.0
    assert per["deepseek-flash"].calls == 2


@case
def tracker_keeps_last_and_resets():
    tracker = UsageTracker()
    price = ModelPrice(input=1.0, output=1.0)
    tracker.record("m", {"prompt_tokens": 10, "completion_tokens": 0}, price, kind="summary")
    last = tracker.last
    assert last is not None and last.kind == "summary"
    tracker.reset()
    assert tracker.last is None
    assert tracker.totals().calls == 0


@case
def summary_calls_are_also_billed():
    """摘要那次调用是真实花销，必须进账本。"""
    tracker = UsageTracker()
    price = ModelPrice(input=9.0, output=27.0)
    tracker.record("m", {"prompt_tokens": 2000, "completion_tokens": 300}, price, kind="chat")
    tracker.record("m", {"prompt_tokens": 4000, "completion_tokens": 200}, price, kind="summary")
    # 两次都算：6000 输入 + 500 输出
    expected = (6000 * 9.0 + 500 * 27.0) / 1_000_000
    assert abs(tracker.totals().cost - expected) < 1e-9, tracker.totals().cost


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"  ERR  {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
