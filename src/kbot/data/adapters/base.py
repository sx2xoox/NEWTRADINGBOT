"""데이터 소스 어댑터 인터페이스. 소스가 바뀌어도 상위 계층은 이 Protocol만 의존한다."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from kbot.core.types import DailyBar, Disclosure, FxRate, InvestorFlow, Market


class DailyBarSource(Protocol):
    name: str

    def supports(self, market: Market) -> bool: ...
    def fetch_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> list[DailyBar]: ...


class DisclosureSource(Protocol):
    name: str

    def fetch_disclosures(self, start: date, end: date, symbol: str | None = None) -> list[Disclosure]: ...


class FxSource(Protocol):
    name: str

    def fetch_usdkrw(self, start: date, end: date) -> list[FxRate]: ...


class InvestorFlowSource(Protocol):
    name: str

    def fetch_flows(self, symbol: str, start: date, end: date) -> list[InvestorFlow]: ...
