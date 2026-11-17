"""도메인 기본 타입."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Market(StrEnum):
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    AMEX = "AMEX"

    @property
    def is_domestic(self) -> bool:
        return self in (Market.KOSPI, Market.KOSDAQ)

    @property
    def currency(self) -> Currency:
        return Currency.KRW if self.is_domestic else Currency.USD


class Currency(StrEnum):
    KRW = "KRW"
    USD = "USD"


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    market: Market
    name: str = ""

    @property
    def key(self) -> str:
        return f"{self.market.value}:{self.symbol}"


@dataclass(frozen=True, slots=True)
class DailyBar:
    """일봉. adjusted=True면 수정주가."""

    symbol: str
    market: Market
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    value: float | None = None   # 거래대금
    adjusted: bool = True
    source: str = ""


@dataclass(frozen=True, slots=True)
class FxRate:
    day: date
    pair: str          # 예: "USDKRW"
    rate: float
    source: str = ""


@dataclass(frozen=True, slots=True)
class InvestorFlow:
    """투자자별 순매수 (금액, 원). 양수 = 순매수."""

    symbol: str
    day: date
    foreign_net: float
    institution_net: float
    individual_net: float
    source: str = ""


@dataclass(frozen=True, slots=True)
class Disclosure:
    """공시/제출서류 메타데이터 (DART·EDGAR 공통)."""

    source: str          # "DART" | "EDGAR"
    doc_id: str          # DART rcept_no / EDGAR accession number
    symbol: str | None
    company: str
    title: str           # DART report_nm / EDGAR form (+items)
    filed_on: date
    url: str
    form_type: str = ""  # EDGAR form, DART pblntf_detail_ty 등
    is_correction: bool = False
    extra: str = ""      # JSON 직렬화된 부가 정보
