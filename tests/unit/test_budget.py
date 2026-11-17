from datetime import date, datetime, timedelta

import pytest
import time_machine

from kbot.core.timeutil import KST
from kbot.llm.budget import BudgetLevel, budget_period
from kbot.llm.errors import BudgetExhausted, DailyLimitExceeded
from kbot.llm.pricing import TokenUsage
from kbot.llm.router import ModelRouter


def kst(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=KST)


# ── 기간 계산 ─────────────────────────────────────────────
@pytest.mark.parametrize("today,reset,start,end", [
    (date(2026, 11, 17), 1, date(2026, 11, 1), date(2026, 12, 1)),
    (date(2026, 11, 17), 20, date(2026, 10, 20), date(2026, 11, 20)),
    (date(2026, 11, 20), 20, date(2026, 11, 20), date(2026, 12, 20)),
    (date(2026, 12, 25), 20, date(2026, 12, 20), date(2027, 1, 20)),
    (date(2026, 1, 5), 20, date(2025, 12, 20), date(2026, 1, 20)),
    # 31일 리셋: 2월엔 말일(28일), 3월엔 31일
    (date(2026, 2, 28), 31, date(2026, 2, 28), date(2026, 3, 31)),
    (date(2026, 2, 27), 31, date(2026, 1, 31), date(2026, 2, 28)),
    (date(2028, 2, 29), 30, date(2028, 2, 29), date(2028, 3, 30)),   # 윤년
])
def test_budget_period(today, reset, start, end):
    assert budget_period(today, reset) == (start, end)


# ── 일일 한도 ─────────────────────────────────────────────
def test_daily_limit_is_remaining_over_remaining_days(make_budget):
    with time_machine.travel(kst(2026, 11, 1), tick=False):
        b = make_budget(150.0, 1)
        st = b.status()
        assert st.remaining_days == 30
        assert st.daily_limit == pytest.approx(5.0)


def test_daily_limit_recalculated_next_day(make_budget, prices):
    with time_machine.travel(kst(2026, 11, 1), tick=False):
        b = make_budget(150.0, 1)
        r = b.reserve("t", 1.0)
        b.settle(r, model="claude-opus-5-5", usage=TokenUsage(output_tokens=50_000))  # $1.00
    with time_machine.travel(kst(2026, 11, 2), tick=False):
        st = b.status()
        assert st.spent_before_today == pytest.approx(1.0)
        assert st.daily_limit == pytest.approx((150 - 1.0) / 29)
        assert st.spent_today == 0


def test_daily_limit_blocks_but_urgent_passes(make_budget):
    with time_machine.travel(kst(2026, 11, 1), tick=False):
        b = make_budget(150.0, 1)       # 일일 한도 $5
        b.reserve("a", 4.0)
        with pytest.raises(DailyLimitExceeded):
            b.reserve("b", 2.0)
        r = b.reserve("risk_alert", 2.0, urgent=True)   # 급한 작업은 일일 한도 초과 허용
        assert r.amount_usd == 2.0


def test_monthly_hard_cap_never_exceeded_even_urgent(make_budget):
    with time_machine.travel(kst(2026, 11, 30), tick=False):   # 마지막 날: 일일 한도 = 월 잔액
        b = make_budget(10.0, 1)
        b.reserve("a", 9.0)
        with pytest.raises(BudgetExhausted):
            b.reserve("b", 1.5, urgent=True)


def test_active_reservations_count_toward_limits(make_budget):
    with time_machine.travel(kst(2026, 11, 30), tick=False):
        b = make_budget(10.0, 1)
        r1 = b.reserve("a", 6.0)
        with pytest.raises(BudgetExhausted):
            b.reserve("b", 6.0)            # 정산 전이라도 예약분으로 막힌다
        b.release(r1)
        b.reserve("b", 6.0)                 # 해제 후에는 가능


def test_settle_records_actual_cost(make_budget, engine):
    from sqlalchemy import select

    from kbot.storage import db

    with time_machine.travel(kst(2026, 11, 5), tick=False):
        b = make_budget()
        r = b.reserve("disclosure_summary", 0.01)
        cost = b.settle(r, model="claude-haiku-5-5", usage=TokenUsage(input_tokens=10_000, output_tokens=500))
        assert cost == pytest.approx((10_000 * 0.10 + 500 * 0.50) / 1e6)
        with engine.connect() as c:
            row = c.execute(select(db.llm_usage)).one()
            res = c.execute(select(db.llm_reservations.c.status)).scalar_one()
        assert row.feature == "disclosure_summary" and row.cost_usd == pytest.approx(cost)
        assert res == "settled"
        st = b.status()
        assert st.reserved == 0 and st.spent == pytest.approx(cost)


def test_release_stale_reservations(make_budget):
    with time_machine.travel(kst(2026, 11, 5), tick=False):
        b = make_budget()
        b.reserve("a", 1.0)
    with time_machine.travel(kst(2026, 11, 6, 18), tick=False):
        assert b.release_stale(timedelta(hours=26)) == 1
        assert b.status().reserved == 0


# ── 단계(70/90/100%) ─────────────────────────────────────
def _spend(b, usd):
    r = b.reserve("x", usd, urgent=True)
    # Opus 출력 1M = $20 → usd 만큼의 출력 토큰
    b.settle(r, model="claude-opus-5-5", usage=TokenUsage(output_tokens=int(usd / 20 * 1_000_000)))


def test_levels_alerts_and_routing(make_budget, notifier, prices, models_cfg):
    router = ModelRouter(models_cfg, prices)
    with time_machine.travel(kst(2026, 11, 30), tick=False):
        b = make_budget(100.0, 1)
        assert router.route("devils_advocate", b.level()).model == "claude-opus-5-5"

        _spend(b, 70.0)
        assert b.level() == BudgetLevel.WARN
        assert [t for t, _, _ in notifier.sent] == ["LLM 예산 70% 도달"]
        assert router.route("devils_advocate", b.level()).model == "claude-opus-5-5"

        _spend(b, 1.0)                      # 같은 단계 재알림 없음
        assert len(notifier.sent) == 1

        _spend(b, 19.0)                     # 90%
        assert b.level() == BudgetLevel.DEGRADED
        route = router.route("devils_advocate", b.level())
        assert route.model == "claude-haiku-5-5" and route.degraded
        assert len(notifier.sent) == 2

        _spend(b, 10.0)                     # 100%
        assert b.level() == BudgetLevel.EXHAUSTED
        assert notifier.sent[-1][2] == "critical"
        with pytest.raises(BudgetExhausted):
            router.route("disclosure_summary", b.level())
        with pytest.raises(BudgetExhausted):
            b.reserve("x", 0.0001, urgent=True)

    # 다음 기간에는 초기화
    with time_machine.travel(kst(2026, 12, 1), tick=False):
        assert b.level() == BudgetLevel.NORMAL
        assert b.status().spent == 0


def test_jump_past_multiple_thresholds_sends_each_alert_once(make_budget, notifier):
    with time_machine.travel(kst(2026, 11, 30), tick=False):
        b = make_budget(100.0, 1)
        _spend(b, 95.0)
        titles = [t for t, _, _ in notifier.sent]
        assert titles == ["LLM 예산 70% 도달", "LLM 예산 90% 도달 — 저가 모델만 사용"]


def test_summary_ko_contains_key_numbers(make_budget):
    with time_machine.travel(kst(2026, 11, 17), tick=False):
        text = make_budget(150.0, 1).status().summary_ko()
        assert "남은 예산" in text and "오늘 한도" in text and "2026-11-01 ~ 2026-11-30" in text


def test_invalid_ratios(engine, prices):
    from kbot.llm.budget import BudgetManager

    with pytest.raises(ValueError):
        BudgetManager(engine, prices, 150, 1, warn_ratio=0.9, degrade_ratio=0.7)
