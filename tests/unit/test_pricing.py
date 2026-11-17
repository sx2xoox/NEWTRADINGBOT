from types import SimpleNamespace

import pytest

from kbot.llm.errors import UnknownModelPricing
from kbot.llm.pricing import TokenUsage, estimate_tokens


def test_opus_cost(prices):
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert prices.cost("claude-opus-5-5", u) == pytest.approx(4.0 + 20.0)


def test_cache_prices_are_explicit_not_multiplier(prices):
    # Opus 5.5 캐시 읽기는 0.05x($0.20) — 0.1x로 가정하면 틀린다
    u = TokenUsage(cache_read_tokens=1_000_000)
    assert prices.cost("claude-opus-5-5", u) == pytest.approx(0.20)
    u = TokenUsage(cache_write_5m_tokens=1_000_000, cache_write_1h_tokens=1_000_000)
    assert prices.cost("claude-sonnet-5-5", u) == pytest.approx(2.50 + 4.00)


def test_haiku_long_prompt_tier_counts_cache_tokens(prices):
    small = TokenUsage(input_tokens=100_000, output_tokens=1000)
    assert prices.cost("claude-haiku-5-5", small) == pytest.approx((100_000 * 0.10 + 1000 * 0.50) / 1e6)
    # 입력 50K + 캐시 읽기 60K = 프롬프트 110K → 요청 전체가 상위 단가
    big = TokenUsage(input_tokens=50_000, cache_read_tokens=60_000, output_tokens=1000)
    expected = (50_000 * 0.50 + 60_000 * 0.05 + 1000 * 2.50) / 1e6
    assert prices.cost("claude-haiku-5-5", big) == pytest.approx(expected)


def test_batch_discount(prices):
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert prices.cost("claude-sonnet-5-5", u, batch=True) == pytest.approx((2 + 10) / 2)


def test_unknown_model_blocked(prices):
    with pytest.raises(UnknownModelPricing):
        prices.cost("claude-unknown", TokenUsage(input_tokens=1))


def test_max_cost_upper_bounds_actual(prices):
    est_prompt, max_out = 5000, 2000
    worst = prices.max_cost("claude-sonnet-5-5", est_prompt, max_out)
    for u in [TokenUsage(input_tokens=5000, output_tokens=2000),
              TokenUsage(cache_write_1h_tokens=5000, output_tokens=2000),
              TokenUsage(cache_read_tokens=4000, input_tokens=1000, output_tokens=1500)]:
        assert prices.cost("claude-sonnet-5-5", u) <= worst + 1e-12


def test_from_api_usage_with_ttl_breakdown():
    usage = SimpleNamespace(input_tokens=10, output_tokens=20, cache_read_input_tokens=30,
                            cache_creation_input_tokens=40,
                            cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=15,
                                                           ephemeral_1h_input_tokens=25))
    u = TokenUsage.from_api_usage(usage)
    assert (u.input_tokens, u.cache_write_5m_tokens, u.cache_write_1h_tokens,
            u.cache_read_tokens, u.output_tokens) == (10, 15, 25, 30, 20)


def test_from_api_usage_without_breakdown_assumes_5m():
    u = TokenUsage.from_api_usage({"input_tokens": 1, "output_tokens": 2, "cache_creation_input_tokens": 7})
    assert u.cache_write_5m_tokens == 7 and u.cache_write_1h_tokens == 0


def test_estimate_tokens_conservative_for_korean():
    text = "삼성전자 유상증자 결정 공시입니다." * 100
    assert estimate_tokens(text) >= len([c for c in text if ord(c) > 127])
