"""LLM 월 예산 하드 제한.

동작 요약
- 예산 기간: 매월 리셋일(LLM_BUDGET_RESET_DAY, 그 달에 없으면 말일)부터 다음 리셋일 전날까지.
- 일일 한도 = (월 예산 − 오늘 이전까지 쓴 금액) ÷ 남은 일수(오늘 포함). KST 0시 기준으로 매일 재계산.
- 호출 전 '최악 비용'을 예약(reserve)하고, 응답 usage로 정산(settle)한다. 예약분도 사용액으로
  계산하므로 동시 호출이 있어도 한도를 넘지 않는다.
- 사용률 70% → 경고(기간당 1회), 90% → 저가 모델만, 100% → LLM 기능 중단.
"""

from __future__ import annotations

import calendar
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from enum import IntEnum

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine

from kbot.core import timeutil
from kbot.core.notify import Notifier
from kbot.llm.errors import BudgetExhausted, DailyLimitExceeded
from kbot.llm.pricing import PriceTable, TokenUsage
from kbot.storage import db

log = logging.getLogger(__name__)


class BudgetLevel(IntEnum):
    NORMAL = 0
    WARN = 1        # ≥ warn_ratio (기본 70%)
    DEGRADED = 2    # ≥ degrade_ratio (기본 90%) — 저가 모델만
    EXHAUSTED = 3   # ≥ 100% — LLM 중단

    @property
    def label_ko(self) -> str:
        return {0: "정상", 1: "경고(70%↑)", 2: "절약 모드(90%↑, 저가 모델만)", 3: "중단(100%)"}[self.value]


def _reset_date(year: int, month: int, reset_day: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(reset_day, last))


def budget_period(today: date, reset_day: int) -> tuple[date, date]:
    """오늘이 속한 예산 기간 [start, end) 반환."""
    this_reset = _reset_date(today.year, today.month, reset_day)
    if today >= this_reset:
        start = this_reset
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        end = _reset_date(ny, nm, reset_day)
    else:
        py, pm = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        start = _reset_date(py, pm, reset_day)
        end = this_reset
    return start, end


@dataclass(frozen=True)
class BudgetStatus:
    period_start: date
    period_end: date            # 미포함
    monthly_budget: float
    spent: float                # 정산 완료 금액
    reserved: float             # 진행 중 예약 금액
    spent_before_today: float
    spent_today: float          # 오늘 정산 + 오늘 예약
    daily_limit: float
    remaining_days: int
    level: BudgetLevel

    @property
    def used(self) -> float:
        return self.spent + self.reserved

    @property
    def used_ratio(self) -> float:
        return self.used / self.monthly_budget

    @property
    def remaining(self) -> float:
        return max(0.0, self.monthly_budget - self.used)

    @property
    def remaining_today(self) -> float:
        return max(0.0, self.daily_limit - self.spent_today)

    def summary_ko(self) -> str:
        last_day = self.period_end - timedelta(days=1)
        return (
            f"예산 기간: {self.period_start} ~ {last_day} (남은 {self.remaining_days}일)\n"
            f"이번 달 사용: ${self.spent:,.4f} (진행 중 예약 ${self.reserved:,.4f}) / 예산 ${self.monthly_budget:,.2f}"
            f" → {self.used_ratio * 100:.1f}%\n"
            f"남은 예산: ${self.remaining:,.4f}\n"
            f"오늘 한도: ${self.daily_limit:,.4f} (사용 ${self.spent_today:,.4f}, 남음 ${self.remaining_today:,.4f})\n"
            f"상태: {self.level.label_ko}"
        )


@dataclass(frozen=True)
class Reservation:
    id: str
    feature: str
    amount_usd: float
    period_start: date
    day_kst: date


class BudgetManager:
    def __init__(
        self,
        engine: Engine,
        prices: PriceTable,
        monthly_budget_usd: float,
        reset_day: int,
        *,
        warn_ratio: float = 0.70,
        degrade_ratio: float = 0.90,
        notifier: Notifier | None = None,
    ) -> None:
        if not 0 < warn_ratio < degrade_ratio < 1:
            raise ValueError("0 < warn_ratio < degrade_ratio < 1 이어야 합니다")
        self.engine = engine
        self.prices = prices
        self.monthly_budget = float(monthly_budget_usd)
        self.reset_day = reset_day
        self.warn_ratio = warn_ratio
        self.degrade_ratio = degrade_ratio
        self.notifier = notifier
        self._lock = threading.Lock()

    # ── 조회 ────────────────────────────────────────────────
    def status(self) -> BudgetStatus:
        today = timeutil.today_kst()
        start, end = budget_period(today, self.reset_day)
        u, r = db.llm_usage, db.llm_reservations
        with self.engine.connect() as conn:
            spent = conn.execute(select(func.coalesce(func.sum(u.c.cost_usd), 0.0))
                                 .where(u.c.period_start == start)).scalar_one()
            spent_today = conn.execute(select(func.coalesce(func.sum(u.c.cost_usd), 0.0))
                                       .where(and_(u.c.period_start == start, u.c.day_kst == today))).scalar_one()
            reserved = conn.execute(select(func.coalesce(func.sum(r.c.amount_usd), 0.0))
                                    .where(and_(r.c.period_start == start, r.c.status == "active"))).scalar_one()
            reserved_today = conn.execute(select(func.coalesce(func.sum(r.c.amount_usd), 0.0))
                                          .where(and_(r.c.period_start == start, r.c.status == "active",
                                                      r.c.day_kst == today))).scalar_one()
        spent_before_today = float(spent) - float(spent_today)
        remaining_days = (end - today).days
        daily_limit = max(0.0, (self.monthly_budget - spent_before_today) / remaining_days)
        used = float(spent) + float(reserved)
        ratio = used / self.monthly_budget
        if ratio >= 1.0:
            level = BudgetLevel.EXHAUSTED
        elif ratio >= self.degrade_ratio:
            level = BudgetLevel.DEGRADED
        elif ratio >= self.warn_ratio:
            level = BudgetLevel.WARN
        else:
            level = BudgetLevel.NORMAL
        return BudgetStatus(
            period_start=start, period_end=end, monthly_budget=self.monthly_budget,
            spent=float(spent), reserved=float(reserved), spent_before_today=spent_before_today,
            spent_today=float(spent_today) + float(reserved_today), daily_limit=daily_limit,
            remaining_days=remaining_days, level=level,
        )

    def level(self) -> BudgetLevel:
        return self.status().level

    # ── 예약 / 정산 ─────────────────────────────────────────
    def reserve(self, feature: str, amount_usd: float, *, urgent: bool = False) -> Reservation:
        """최악 비용을 예약. 월 잔액 초과 시 BudgetExhausted, 일일 한도 초과 시 DailyLimitExceeded.

        urgent=True(예: 보유 종목 위험 공시 요약)는 일일 한도는 넘을 수 있지만 월 예산은 절대 넘지 못한다.
        """
        if amount_usd < 0:
            raise ValueError("예약 금액은 음수일 수 없습니다")
        with self._lock:
            st = self.status()
            if st.level == BudgetLevel.EXHAUSTED or st.used + amount_usd > self.monthly_budget:
                raise BudgetExhausted(
                    f"월 LLM 예산 한도로 호출을 차단했습니다 (사용 ${st.used:.4f} + 예상 ${amount_usd:.4f}"
                    f" > 예산 ${self.monthly_budget:.2f})")
            if not urgent and st.spent_today + amount_usd > st.daily_limit:
                raise DailyLimitExceeded(
                    f"오늘 LLM 한도를 넘어 호출을 미룹니다 (오늘 ${st.spent_today:.4f} + 예상 ${amount_usd:.4f}"
                    f" > 한도 ${st.daily_limit:.4f})")
            today = timeutil.today_kst()
            res = Reservation(id=str(uuid.uuid4()), feature=feature, amount_usd=amount_usd,
                              period_start=st.period_start, day_kst=today)
            with self.engine.begin() as conn:
                conn.execute(db.llm_reservations.insert().values(
                    id=res.id, created_at=timeutil.now(), period_start=res.period_start, day_kst=today,
                    feature=feature, amount_usd=amount_usd, status="active"))
            return res

    def settle(self, res: Reservation, *, model: str, usage: TokenUsage, batch: bool = False,
               request_id: str | None = None, note: str = "") -> float:
        """실제 사용량으로 정산하고 비용을 원장에 기록. 반환: 실제 비용(USD)."""
        cost = self.prices.cost(model, usage, batch=batch)
        with self._lock:
            with self.engine.begin() as conn:
                conn.execute(db.llm_reservations.update()
                             .where(db.llm_reservations.c.id == res.id).values(status="settled"))
                conn.execute(db.llm_usage.insert().values(
                    created_at=timeutil.now(), period_start=res.period_start, day_kst=res.day_kst,
                    feature=res.feature, model=model, batch=batch,
                    input_tokens=usage.input_tokens, cache_write_5m_tokens=usage.cache_write_5m_tokens,
                    cache_write_1h_tokens=usage.cache_write_1h_tokens,
                    cache_read_tokens=usage.cache_read_tokens, output_tokens=usage.output_tokens,
                    cost_usd=cost, request_id=request_id, note=note[:200]))
        if cost > res.amount_usd * 1.001:
            log.warning("LLM 실제 비용이 예약액을 초과: feature=%s 예약 $%.5f 실제 $%.5f",
                        res.feature, res.amount_usd, cost)
        self._maybe_alert()
        return cost

    def release(self, res: Reservation) -> None:
        """호출이 실패해 과금되지 않은 예약을 해제."""
        with self._lock, self.engine.begin() as conn:
            conn.execute(db.llm_reservations.update()
                         .where(and_(db.llm_reservations.c.id == res.id,
                                     db.llm_reservations.c.status == "active"))
                         .values(status="released"))

    def release_stale(self, older_than: timedelta = timedelta(hours=26)) -> int:
        """프로세스가 죽어 남은 예약 정리. 배치 작업은 최대 24시간이 걸릴 수 있어 여유를 둔다."""
        cutoff = timeutil.now() - older_than
        r = db.llm_reservations
        with self._lock, self.engine.begin() as conn:
            res = conn.execute(r.update().where(and_(r.c.status == "active", r.c.created_at < cutoff))
                               .values(status="released"))
            return res.rowcount or 0

    # ── 알림 ────────────────────────────────────────────────
    def _maybe_alert(self) -> None:
        st = self.status()
        if st.level < BudgetLevel.WARN or self.notifier is None:
            return
        a = db.llm_budget_alerts
        with self.engine.begin() as conn:
            sent = set(conn.execute(select(a.c.level).where(a.c.period_start == st.period_start)).scalars())
            to_send = [lv for lv in (BudgetLevel.WARN, BudgetLevel.DEGRADED, BudgetLevel.EXHAUSTED)
                       if lv <= st.level and lv.name not in sent]
            for lv in to_send:
                conn.execute(a.insert().values(period_start=st.period_start, level=lv.name,
                                               sent_at=timeutil.now()))
        for lv in to_send:
            title, level = {
                BudgetLevel.WARN: ("LLM 예산 70% 도달", "warning"),
                BudgetLevel.DEGRADED: ("LLM 예산 90% 도달 — 저가 모델만 사용", "warning"),
                BudgetLevel.EXHAUSTED: ("LLM 예산 소진 — LLM 기능 중단 (매매·리스크 엔진은 계속 동작)", "critical"),
            }[lv]
            self.notifier.send(title, st.summary_ko(), level)
