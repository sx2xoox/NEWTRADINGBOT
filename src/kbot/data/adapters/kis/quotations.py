"""KIS 시세 조회 (일봉).

- 국내: GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice  tr_id FHKST03010100
        (1회 최대 100건, output2: stck_bsop_date/stck_oprc/stck_hgpr/stck_lwpr/stck_clpr/acml_vol/acml_tr_pbmn)
- 해외: GET /uapi/overseas-price/v1/quotations/dailyprice  tr_id HHDFS76240000
        (BYMD 기준 과거로 최대 100건, output2: xymd/open/high/low/clos/tvol/tamt)
응답의 rt_cd != "0" 이면 오류 (msg_cd, msg1). EGW00201 = 초당 거래건수 초과 → 잠시 후 재시도.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from kbot.core.types import DailyBar, Market
from kbot.data.adapters.http import DataSourceError, HttpFetcher
from kbot.data.adapters.kis.auth import KisCredentials, KisTokenManager

log = logging.getLogger(__name__)

DOMESTIC_DAILY = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
OVERSEAS_DAILY = "/uapi/overseas-price/v1/quotations/dailyprice"
EXCD = {Market.NYSE: "NYS", Market.NASDAQ: "NAS", Market.AMEX: "AMS"}
RATE_LIMIT_CODE = "EGW00201"


class KisError(DataSourceError):
    pass


def _f(v: str | None) -> float:
    return float(v) if v not in (None, "") else 0.0


class KisQuotationAdapter:
    name = "KIS"

    def __init__(self, creds: KisCredentials, token_cache: Path, *, fetcher: HttpFetcher | None = None,
                 min_interval_sec: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> None:
        self.creds = creds
        # KIS는 업무 오류(예: EGW00201 초당 건수 초과)를 HTTP 500 + JSON 본문으로 돌려주므로
        # 500은 공통 재시도에서 빼고 _get에서 본문(msg_cd)을 보고 처리한다.
        self.http = fetcher or HttpFetcher("KIS", min_interval_sec=min_interval_sec, sleep=sleep,
                                           retry_statuses={429, 502, 503, 504})
        self.tokens = KisTokenManager(creds, token_cache, self.http)
        self._sleep = sleep

    def supports(self, market: Market) -> bool:
        return True

    def _get(self, path: str, tr_id: str, params: dict, *, retries: int = 3) -> dict:
        for attempt in range(retries + 1):
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self.tokens.token()}",
                "appkey": self.creds.app_key,
                "appsecret": self.creds.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            resp = self.http.get(f"{self.creds.domain}{path}", params=params, headers=headers)
            try:
                data = resp.json()
            except ValueError as e:
                raise KisError(f"KIS 응답 파싱 실패 (HTTP {resp.status_code})") from e
            if str(data.get("rt_cd")) == "0":
                return data
            if data.get("msg_cd") == RATE_LIMIT_CODE and attempt < retries:
                self._sleep(1.0 * (attempt + 1))
                continue
            raise KisError(f"KIS 오류 {data.get('msg_cd', '')}: {data.get('msg1', '')}".strip())
        raise KisError("KIS 요청 재시도 초과")

    def fetch_daily_bars(self, market: Market, symbol: str, start: date, end: date) -> list[DailyBar]:
        bars = (self._domestic(market, symbol, start, end) if market.is_domestic
                else self._overseas(market, symbol, start, end))
        uniq = {b.day: b for b in bars if start <= b.day <= end}
        return [uniq[d] for d in sorted(uniq)]

    def _domestic(self, market: Market, symbol: str, start: date, end: date) -> list[DailyBar]:
        out: list[DailyBar] = []
        cur_end = end
        while cur_end >= start:
            # 130 달력일 ≈ 거래일 100일 이하 → 1회 최대 100건 제한 안에 들어온다
            cur_start = max(start, cur_end - timedelta(days=130))
            data = self._get(DOMESTIC_DAILY, "FHKST03010100", {
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": cur_start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": cur_end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",   # 0 = 수정주가
            })
            for r in data.get("output2") or []:
                if not r.get("stck_bsop_date"):
                    continue
                out.append(DailyBar(
                    symbol=symbol, market=market, day=datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date(),
                    open=_f(r.get("stck_oprc")), high=_f(r.get("stck_hgpr")), low=_f(r.get("stck_lwpr")),
                    close=_f(r.get("stck_clpr")), volume=_f(r.get("acml_vol")),
                    value=_f(r.get("acml_tr_pbmn")), adjusted=True, source="KIS"))
            cur_end = cur_start - timedelta(days=1)
        return out

    def _overseas(self, market: Market, symbol: str, start: date, end: date) -> list[DailyBar]:
        out: list[DailyBar] = []
        bymd = end
        for _ in range(200):   # 안전장치
            data = self._get(OVERSEAS_DAILY, "HHDFS76240000", {
                "AUTH": "", "EXCD": EXCD[market], "SYMB": symbol, "GUBN": "0",
                "BYMD": bymd.strftime("%Y%m%d"), "MODP": "1",       # 1 = 수정주가 반영
            })
            rows = [r for r in (data.get("output2") or []) if r.get("xymd")]
            if not rows:
                break
            for r in rows:
                out.append(DailyBar(
                    symbol=symbol, market=market, day=datetime.strptime(r["xymd"], "%Y%m%d").date(),
                    open=_f(r.get("open")), high=_f(r.get("high")), low=_f(r.get("low")),
                    close=_f(r.get("clos")), volume=_f(r.get("tvol")), value=_f(r.get("tamt")),
                    adjusted=True, source="KIS"))
            earliest = min(datetime.strptime(r["xymd"], "%Y%m%d").date() for r in rows)
            if earliest <= start:
                break
            bymd = earliest - timedelta(days=1)
        return out
