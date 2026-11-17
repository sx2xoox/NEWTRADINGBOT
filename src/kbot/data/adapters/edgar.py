"""SEC EDGAR 어댑터.

- 티커→CIK: https://www.sec.gov/files/company_tickers.json
- 제출 목록: https://data.sec.gov/submissions/CIK##########.json (filings.recent 배열)
- SEC 공정 접근 정책: 연락처가 담긴 User-Agent 필수, 초당 10건 이하.
"""

from __future__ import annotations

from datetime import date, datetime

from kbot.core.types import Disclosure
from kbot.data.adapters.http import DataSourceError, HttpFetcher
from kbot.storage.repositories import dumps_extra

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
DEFAULT_FORMS = ("8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "6-K", "20-F", "13F-HR", "13F-HR/A")

# 8-K 항목 번호 → 한국어 설명 (브리핑·경보에서 사용)
EIGHT_K_ITEMS_KO = {
    "1.01": "중요 계약 체결", "1.02": "중요 계약 해지", "1.03": "파산·회생",
    "2.01": "자산 인수·처분 완료", "2.02": "실적 발표", "2.03": "중요 채무 발생",
    "2.05": "구조조정 비용", "2.06": "중대한 손상차손", "3.01": "상장폐지·상장요건 미달 통지",
    "3.02": "미등록 주식 발행", "3.03": "주주 권리 변경", "4.01": "감사인 변경",
    "4.02": "과거 재무제표 신뢰 불가(재작성)", "5.01": "지배권 변동", "5.02": "임원·이사 변동",
    "5.03": "정관 변경", "5.07": "주총 표결 결과", "7.01": "Reg FD 공시", "8.01": "기타 중요 사항",
    "9.01": "재무제표·첨부",
}


class EdgarAdapter:
    name = "EDGAR"

    def __init__(self, user_agent: str, fetcher: HttpFetcher | None = None, min_interval_sec: float = 0.15) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT에 이름과 이메일을 넣어야 합니다 (SEC 정책)")
        self.http = fetcher or HttpFetcher("EDGAR", min_interval_sec=min_interval_sec)
        self.http.client.headers["User-Agent"] = user_agent
        self.http.client.headers["Accept-Encoding"] = "gzip, deflate"

    def fetch_ticker_map(self) -> list[dict]:
        resp = self.http.get(TICKERS_URL)
        if resp.status_code != 200:
            raise DataSourceError(f"EDGAR 티커 목록 HTTP {resp.status_code}")
        return [{"ticker": v["ticker"].upper(), "cik": int(v["cik_str"]), "title": v["title"]}
                for v in resp.json().values()]

    def fetch_filings(self, cik: int, start: date, end: date, *, ticker: str | None = None,
                      forms: tuple[str, ...] = DEFAULT_FORMS) -> list[Disclosure]:
        """최근 제출 목록(filings.recent, 보통 최근 1천 건 이상)에서 기간·양식으로 거른다."""
        resp = self.http.get(SUBMISSIONS_URL.format(cik=cik))
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            raise DataSourceError(f"EDGAR 제출목록 HTTP {resp.status_code}")
        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        company = data.get("name", "")
        n = len(recent.get("accessionNumber", []))
        out: list[Disclosure] = []
        for i in range(n):
            form = recent["form"][i]
            if forms and form not in forms:
                continue
            filed = datetime.strptime(recent["filingDate"][i], "%Y-%m-%d").date()
            if not (start <= filed <= end):
                continue
            acc = recent["accessionNumber"][i]
            doc = (recent.get("primaryDocument") or [""] * n)[i]
            items = (recent.get("items") or [""] * n)[i] or ""
            title = form + (f" (Items {items})" if items else "")
            out.append(Disclosure(
                source="EDGAR", doc_id=acc, symbol=ticker.upper() if ticker else None,
                company=company, title=title, filed_on=filed,
                url=ARCHIVE_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc),
                form_type=form, is_correction=form.endswith("/A"),
                extra=dumps_extra({"cik": cik, "items": items,
                                   "accepted": (recent.get("acceptanceDateTime") or [""] * n)[i],
                                   "report_date": (recent.get("reportDate") or [""] * n)[i]}),
            ))
        return out

    def fetch_document(self, url: str) -> str:
        resp = self.http.get(url)
        if resp.status_code != 200:
            raise DataSourceError(f"EDGAR 문서 HTTP {resp.status_code}")
        return resp.text
