"""pykrx 어댑터 — 국내 일봉(과거), 투자자별 순매수(원), 과거 시점 상장 종목 목록(생존 편향 방지).

KRX 정보데이터시스템이 로그인을 요구하므로 KRX_ID/KRX_PW가 필요하다(pykrx 1.2.9+가 자동 로그인).
pykrx는 import 시점에 로그인을 시도하므로 반드시 지연 import 한다.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from kbot.core.types import DailyBar, InvestorFlow, Market
from kbot.data.adapters.http import DataSourceError

_MARKET_ARG = {Market.KOSPI: "KOSPI", Market.KOSDAQ: "KOSDAQ"}


class PykrxAdapter:
    name = "PYKRX"

    def __init__(self, krx_id: str | None = None, krx_pw: str | None = None, stock_module: Any = None) -> None:
        self._id, self._pw = krx_id, krx_pw
        self._stock = stock_module

    def _api(self) -> Any:
        if self._stock is None:
            if self._id and self._pw:
                os.environ.setdefault("KRX_ID", self._id)
                os.environ.setdefault("KRX_PW", self._pw)
            from pykrx import stock  # noqa: PLC0415 — 지연 import (import 시 로그인 시도)

            self._stock = stock
        return self._stock

    def supports(self, market: Market) -> bool:
        return market.is_domestic

    def fetch_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> list[DailyBar]:
        df = self._api().get_market_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), symbol, adjusted=True)
        if df is None or df.empty:
            return []
        out = []
        for idx, r in df.iterrows():
            if float(r["거래량"]) == 0 and float(r["시가"]) == 0:   # 거래정지일
                continue
            out.append(DailyBar(
                symbol=symbol, market=market, day=idx.date(), open=float(r["시가"]), high=float(r["고가"]),
                low=float(r["저가"]), close=float(r["종가"]), volume=float(r["거래량"]),
                value=float(r["거래대금"]) if "거래대금" in r else None, adjusted=True, source="PYKRX"))
        return out

    def fetch_flows(self, symbol: str, start: date, end: date) -> list[InvestorFlow]:
        """투자자별 순매수 거래대금 (단위: 원)."""
        df = self._api().get_market_trading_value_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), symbol)
        if df is None or df.empty:
            return []
        missing = {"기관합계", "개인", "외국인합계"} - set(df.columns)
        if missing:
            raise DataSourceError(f"pykrx 수급 컬럼 누락: {sorted(missing)}")
        return [InvestorFlow(symbol=symbol, day=idx.date(), foreign_net=float(r["외국인합계"]),
                             institution_net=float(r["기관합계"]), individual_net=float(r["개인"]),
                             source="PYKRX") for idx, r in df.iterrows()]

    def listed_tickers(self, day: date, market: Market) -> list[str]:
        """그 날짜 기준 상장 종목 (이후 상장폐지된 종목 포함 → 백테스트 유니버스 구성에 사용)."""
        return list(self._api().get_market_ticker_list(day.strftime("%Y%m%d"), market=_MARKET_ARG[market]))
