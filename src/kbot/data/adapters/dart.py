"""DART OpenAPI 어댑터.

공식 가이드(https://opendart.fss.or.kr/guide) 기준:
- 공시검색  GET /api/list.json     (page_count 최대 100, corp_code 없으면 검색기간 3개월 제한)
- 원본파일  GET /api/document.xml  (ZIP)
- 고유번호  GET /api/corpCode.xml  (ZIP 안 CORPCODE.xml)
상태코드: 000 정상, 013 데이터 없음, 020 요청 제한 초과 등.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from kbot.core.types import Disclosure
from kbot.data.adapters.http import DataSourceError, HttpFetcher
from kbot.storage.repositories import dumps_extra

log = logging.getLogger(__name__)

BASE = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
_STATUS_MSG = {
    "010": "등록되지 않은 키", "011": "사용할 수 없는 키", "012": "접근할 수 없는 IP",
    "014": "파일이 존재하지 않음", "020": "요청 제한 초과", "021": "조회 가능한 회사 개수 초과",
    "100": "필드 값 오류", "101": "부적절한 접근", "800": "시스템 점검 중", "900": "정의되지 않은 오류",
    "901": "개인정보 보유기간 만료 키",
}
CORRECTION_TAGS = ("[기재정정]", "[첨부정정]", "[첨부추가]", "[발행조건확정]")


class DartError(DataSourceError):
    pass


def _check_status(payload: dict) -> bool:
    """정상이면 True, 데이터 없음(013)이면 False, 그 외는 예외."""
    status = str(payload.get("status", ""))
    if status == "000":
        return True
    if status == "013":
        return False
    raise DartError(f"DART 오류 {status}: {_STATUS_MSG.get(status, payload.get('message', ''))}")


def _date_chunks(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    cur = start
    while cur <= end:
        nxt = min(end, cur + timedelta(days=days - 1))
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def parse_list_item(item: dict) -> Disclosure:
    report_nm = (item.get("report_nm") or "").strip()
    stock = (item.get("stock_code") or "").strip() or None
    return Disclosure(
        source="DART",
        doc_id=item["rcept_no"],
        symbol=stock,
        company=item.get("corp_name", ""),
        title=report_nm,
        filed_on=datetime.strptime(item["rcept_dt"], "%Y%m%d").date(),
        url=VIEWER.format(item["rcept_no"]),
        form_type=item.get("pblntf_detail_ty", "") or "",
        is_correction=any(tag in report_nm for tag in CORRECTION_TAGS) or report_nm.startswith("[정정"),
        extra=dumps_extra({k: item.get(k, "") for k in ("corp_code", "corp_cls", "flr_nm", "rm")}),
    )


class DartAdapter:
    name = "DART"

    def __init__(self, api_key: str, fetcher: HttpFetcher | None = None, min_interval_sec: float = 0.2) -> None:
        if not api_key:
            raise ValueError("DART_API_KEY가 필요합니다")
        self._key = api_key
        self.http = fetcher or HttpFetcher("DART", min_interval_sec=min_interval_sec)

    def _get_json(self, path: str, params: dict) -> dict:
        resp = self.http.get(f"{BASE}/{path}", params={"crtfc_key": self._key, **params})
        if resp.status_code != 200:
            raise DartError(f"DART HTTP {resp.status_code}")
        return resp.json()

    def _get_zip(self, path: str, params: dict) -> zipfile.ZipFile:
        resp = self.http.get(f"{BASE}/{path}", params={"crtfc_key": self._key, **params})
        if resp.status_code != 200:
            raise DartError(f"DART HTTP {resp.status_code}")
        content = resp.content
        if not content.startswith(b"PK"):   # ZIP이 아니면 오류 XML/JSON
            try:
                root = ET.fromstring(content)
                _check_status({"status": root.findtext("status"), "message": root.findtext("message")})
            except ET.ParseError:
                pass
            raise DartError("DART 응답이 ZIP 파일이 아닙니다")
        return zipfile.ZipFile(io.BytesIO(content))

    # ── 고유번호 ────────────────────────────────────────────
    def fetch_corp_codes(self) -> list[dict]:
        zf = self._get_zip("corpCode.xml", {})
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(zf.read(name))
        rows = []
        for el in root.iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            rows.append({
                "corp_code": (el.findtext("corp_code") or "").strip(),
                "corp_name": (el.findtext("corp_name") or "").strip(),
                "stock_code": stock or None,
                "modify_date": (el.findtext("modify_date") or "").strip() or None,
            })
        return rows

    # ── 공시검색 ────────────────────────────────────────────
    def fetch_disclosures(self, start: date, end: date, symbol: str | None = None,
                          *, corp_code: str | None = None, pblntf_ty: str | None = None) -> list[Disclosure]:
        """기간 내 공시 목록. symbol 필터는 corp_code 없이 쓸 때 결과에서 거른다."""
        out: list[Disclosure] = []
        # corp_code 없이 검색하면 3개월 제한 → 보수적으로 89일 단위로 나눈다
        chunk_days = 3650 if corp_code else 89
        for s, e in _date_chunks(start, end, chunk_days):
            page = 1
            while True:
                params = {"bgn_de": s.strftime("%Y%m%d"), "end_de": e.strftime("%Y%m%d"),
                          "page_no": str(page), "page_count": "100"}
                if corp_code:
                    params["corp_code"] = corp_code
                if pblntf_ty:
                    params["pblntf_ty"] = pblntf_ty
                payload = self._get_json("list.json", params)
                if not _check_status(payload):
                    break
                out.extend(parse_list_item(it) for it in payload.get("list", []))
                if page >= int(payload.get("total_page", 1) or 1):
                    break
                page += 1
        if symbol:
            out = [d for d in out if d.symbol == symbol]
        return out

    # ── 원본 문서 ───────────────────────────────────────────
    def fetch_document_files(self, rcept_no: str) -> dict[str, bytes]:
        """공시서류 원본 ZIP의 파일들 (파일명 → 바이트). 텍스트 추출은 3단계 공시 해석 모듈에서."""
        zf = self._get_zip("document.xml", {"rcept_no": rcept_no})
        return {n: zf.read(n) for n in zf.namelist()}
