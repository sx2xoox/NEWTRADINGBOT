"""한국은행 ECOS 어댑터 — 원/달러 매매기준율 (통계표 731Y001, 항목 0000001, 일별).

URL 형식: /api/StatisticSearch/{KEY}/json/kr/{start}/{end}/{STAT}/{CYCLE}/{from}/{to}/{ITEM}
"""

from __future__ import annotations

from datetime import date, datetime

from kbot.core.types import FxRate
from kbot.data.adapters.http import DataSourceError, HttpFetcher

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
STAT_USDKRW = ("731Y001", "0000001")
PAGE = 1000


class EcosAdapter:
    name = "ECOS"

    def __init__(self, api_key: str, fetcher: HttpFetcher | None = None) -> None:
        if not api_key:
            raise ValueError("ECOS_API_KEY가 필요합니다")
        self._key = api_key
        self.http = fetcher or HttpFetcher("ECOS", min_interval_sec=0.2)

    def fetch_usdkrw(self, start: date, end: date) -> list[FxRate]:
        stat, item = STAT_USDKRW
        out: list[FxRate] = []
        first = 1
        while True:
            url = (f"{BASE}/{self._key}/json/kr/{first}/{first + PAGE - 1}/{stat}/D/"
                   f"{start:%Y%m%d}/{end:%Y%m%d}/{item}")
            resp = self.http.get(url)
            if resp.status_code != 200:
                raise DataSourceError(f"ECOS HTTP {resp.status_code}")
            payload = resp.json()
            if "RESULT" in payload:   # 오류 또는 데이터 없음
                code = payload["RESULT"].get("CODE", "")
                if code == "INFO-200":   # 해당하는 데이터가 없음
                    break
                raise DataSourceError(f"ECOS 오류 {code}: {payload['RESULT'].get('MESSAGE', '')}")
            body = payload["StatisticSearch"]
            for row in body.get("row", []):
                v = row.get("DATA_VALUE")
                if v in (None, "", "-"):
                    continue
                out.append(FxRate(day=datetime.strptime(row["TIME"], "%Y%m%d").date(),
                                  pair="USDKRW", rate=float(v), source="ECOS"))
            total = int(body.get("list_total_count", 0))
            first += PAGE
            if first > total:
                break
        return out
