"""yfinance 어댑터 — 미국 일봉 과거 데이터 (무료, 상장폐지 종목 데이터 부족 → 생존 편향 있음).

리포트에 '미국 데이터는 생존 편향 있음'을 명시한다 (1단계 결정 사항 1-a).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from kbot.core.types import DailyBar, Market

SURVIVORSHIP_NOTE = "미국 데이터(yfinance)는 상장폐지 종목이 빠져 있어 생존 편향이 있습니다."


class YFinanceAdapter:
    name = "YFINANCE"

    def __init__(self, yf_module: Any = None) -> None:
        self._yf = yf_module

    def _api(self) -> Any:
        if self._yf is None:
            import yfinance  # noqa: PLC0415

            self._yf = yfinance
        return self._yf

    def supports(self, market: Market) -> bool:
        return not market.is_domestic

    def fetch_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> list[DailyBar]:
        # end는 미포함이므로 +1일. auto_adjust=True → 분할·배당 반영 수정주가
        df = self._api().download(symbol, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                  auto_adjust=True, progress=False, multi_level_index=False)
        if df is None or df.empty:
            return []
        return [DailyBar(symbol=symbol, market=market, day=idx.date(), open=float(r["Open"]),
                         high=float(r["High"]), low=float(r["Low"]), close=float(r["Close"]),
                         volume=float(r["Volume"]), value=None, adjusted=True, source="YFINANCE")
                for idx, r in df.iterrows()]
