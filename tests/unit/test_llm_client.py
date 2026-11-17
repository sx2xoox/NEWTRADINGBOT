from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import time_machine

from kbot.core.timeutil import KST
from kbot.llm.client import LLMClient
from kbot.llm.errors import BudgetExhausted, LLMCallFailed, PromptSecretLeak
from kbot.llm.router import ModelRouter

NOW = datetime(2026, 11, 17, 9, tzinfo=KST)


class FakeMessages:
    def __init__(self, behaviour="ok", stop_reason="end_turn"):
        self.calls = []
        self.behaviour = behaviour
        self.stop_reason = stop_reason

    def create(self, **kw):
        self.calls.append(kw)
        if self.behaviour == "status_error":
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.InternalServerError("boom", response=httpx.Response(500, request=req), body=None)
        if self.behaviour == "conn_error":
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        return SimpleNamespace(
            id="msg_1", model=kw["model"], stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="요약 결과")],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0, cache_creation=None),
        )


def build(engine, make_budget, prices, models_cfg, msgs, monthly=150.0, secrets=()):
    budget = make_budget(monthly, 1)
    router = ModelRouter(models_cfg, prices)
    sdk = SimpleNamespace(messages=msgs)
    return LLMClient(engine, budget, router, api_key="sk-ant-test-key-123456", secrets=secrets,
                     sdk_factory=lambda: sdk), budget


@time_machine.travel(NOW, tick=False)
def test_complete_settles_cost_and_routes(engine, make_budget, prices, models_cfg):
    msgs = FakeMessages()
    client, budget = build(engine, make_budget, prices, models_cfg, msgs)
    res = client.complete(feature="disclosure_summary", system="시스템", user="공시 본문")
    assert res.text == "요약 결과" and res.model == "claude-haiku-5-5" and not res.cached
    assert res.cost_usd == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    call = msgs.calls[0]
    assert call["output_config"] == {"effort": "low"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    st = budget.status()
    assert st.spent == pytest.approx(res.cost_usd) and st.reserved == 0


@time_machine.travel(NOW, tick=False)
def test_cache_prevents_duplicate_call(engine, make_budget, prices, models_cfg):
    msgs = FakeMessages()
    client, budget = build(engine, make_budget, prices, models_cfg, msgs)
    kw = dict(feature="disclosure_summary", system="s", user="u", cache_key="20261117000123")
    first = client.complete(**kw)
    second = client.complete(**kw)
    assert len(msgs.calls) == 1
    assert second.cached and second.text == first.text and second.cost_usd == 0
    # 프롬프트 버전이 바뀌면 다시 호출
    client.complete(**{**kw, "prompt_version": "v2"})
    assert len(msgs.calls) == 2


@time_machine.travel(NOW, tick=False)
def test_secret_in_prompt_blocked_before_any_spend(engine, make_budget, prices, models_cfg):
    msgs = FakeMessages()
    client, budget = build(engine, make_budget, prices, models_cfg, msgs, secrets=["DARTKEY_abcdef0123"])
    for leaked in ["sk-ant-test-key-123456", "DARTKEY_abcdef0123"]:
        with pytest.raises(PromptSecretLeak):
            client.complete(feature="x", system="s", user=f"키는 {leaked} 입니다")
    assert msgs.calls == [] and budget.status().used == 0


@pytest.mark.parametrize("behaviour", ["status_error", "conn_error"])
@time_machine.travel(NOW, tick=False)
def test_api_failure_releases_reservation(engine, make_budget, prices, models_cfg, behaviour):
    client, budget = build(engine, make_budget, prices, models_cfg, FakeMessages(behaviour))
    with pytest.raises(LLMCallFailed):
        client.complete(feature="x", system="s", user="u")
    st = budget.status()
    assert st.reserved == 0 and st.spent == 0


@time_machine.travel(NOW, tick=False)
def test_refusal_is_billed_but_raises(engine, make_budget, prices, models_cfg):
    client, budget = build(engine, make_budget, prices, models_cfg, FakeMessages(stop_reason="refusal"))
    with pytest.raises(LLMCallFailed):
        client.complete(feature="x", system="s", user="u")
    assert budget.status().spent > 0     # 거절도 토큰을 썼다면 원장에 남긴다


@time_machine.travel(NOW, tick=False)
def test_truncated_response_not_cached(engine, make_budget, prices, models_cfg):
    msgs = FakeMessages(stop_reason="max_tokens")
    client, _ = build(engine, make_budget, prices, models_cfg, msgs)
    r = client.complete(feature="x", system="s", user="u", cache_key="k")
    assert r.truncated
    client.complete(feature="x", system="s", user="u", cache_key="k")
    assert len(msgs.calls) == 2


@time_machine.travel(datetime(2026, 11, 30, 9, tzinfo=KST), tick=False)
def test_exhausted_budget_makes_no_api_call(engine, make_budget, prices, models_cfg):
    msgs = FakeMessages()
    client, budget = build(engine, make_budget, prices, models_cfg, msgs, monthly=0.001)
    with pytest.raises(BudgetExhausted):
        client.complete(feature="devils_advocate", system="s" * 2000, user="u" * 2000, max_tokens=8000)
    assert msgs.calls == []


@time_machine.travel(datetime(2026, 11, 30, 9, tzinfo=KST), tick=False)
def test_degraded_budget_uses_cheap_model(engine, make_budget, prices, models_cfg):
    from kbot.llm.pricing import TokenUsage

    msgs = FakeMessages()
    client, budget = build(engine, make_budget, prices, models_cfg, msgs, monthly=10.0)
    r = budget.reserve("x", 9.5, urgent=True)
    budget.settle(r, model="claude-opus-5-5", usage=TokenUsage(output_tokens=475_000))  # $9.5 = 95%
    res = client.complete(feature="devils_advocate", system="s", user="u")
    assert res.degraded and msgs.calls[0]["model"] == "claude-haiku-5-5"


@time_machine.travel(NOW, tick=False)
def test_complete_json_requires_schema_and_parses(engine, make_budget, prices, models_cfg):
    msgs = FakeMessages()
    client, _ = build(engine, make_budget, prices, models_cfg, msgs)
    with pytest.raises(ValueError):
        client.complete_json(feature="x", system="s", user="u")
    with pytest.raises(LLMCallFailed):   # "요약 결과"는 JSON이 아님
        client.complete_json(feature="x", system="s", user="u", json_schema={"type": "object"})
    assert msgs.calls[0]["output_config"]["format"]["type"] == "json_schema"
