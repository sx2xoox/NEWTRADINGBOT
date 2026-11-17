import io
import zipfile
from datetime import date

import httpx
import pytest
import respx

from kbot.data.adapters.dart import BASE, DartAdapter, DartError
from kbot.data.adapters.http import HttpFetcher


def adapter():
    return DartAdapter("K" * 40, fetcher=HttpFetcher("DART", min_interval_sec=0, sleep=lambda s: None))


def item(no, nm, stock="005930"):
    return {"corp_cls": "Y", "corp_name": "삼성전자", "corp_code": "00126380", "stock_code": stock,
            "report_nm": nm, "rcept_no": no, "flr_nm": "삼성전자", "rcept_dt": no[:8], "rm": "유"}


@respx.mock
def test_list_pagination_and_parsing():
    route = respx.get(f"{BASE}/list.json").mock(side_effect=[
        httpx.Response(200, json={"status": "000", "total_page": 2, "list": [item("20261110000001", "유상증자결정")]}),
        httpx.Response(200, json={"status": "000", "total_page": 2,
                                  "list": [item("20261111000002", "[기재정정]유상증자결정")]}),
    ])
    out = adapter().fetch_disclosures(date(2026, 11, 1), date(2026, 11, 17), corp_code="00126380")
    assert route.call_count == 2
    assert [d.doc_id for d in out] == ["20261110000001", "20261111000002"]
    assert out[0].symbol == "005930" and not out[0].is_correction
    assert out[1].is_correction
    assert out[0].url.endswith("rcpNo=20261110000001")
    assert out[0].filed_on == date(2026, 11, 10)
    q = route.calls[0].request.url.params
    assert q["page_count"] == "100" and q["corp_code"] == "00126380"


@respx.mock
def test_without_corp_code_splits_into_3_month_windows():
    route = respx.get(f"{BASE}/list.json").mock(return_value=httpx.Response(200, json={"status": "013"}))
    assert adapter().fetch_disclosures(date(2026, 1, 1), date(2026, 12, 31)) == []
    assert route.call_count == 5   # 365일 / 89일


@respx.mock
def test_error_status_raises_korean_message():
    respx.get(f"{BASE}/list.json").mock(return_value=httpx.Response(200, json={"status": "020"}))
    with pytest.raises(DartError, match="요청 제한 초과"):
        adapter().fetch_disclosures(date(2026, 11, 1), date(2026, 11, 2), corp_code="1")


@respx.mock
def test_error_does_not_leak_key():
    respx.get(f"{BASE}/list.json").mock(return_value=httpx.Response(200, json={"status": "010"}))
    with pytest.raises(DartError) as ei:
        adapter().fetch_disclosures(date(2026, 11, 1), date(2026, 11, 2), corp_code="1")
    assert "K" * 40 not in str(ei.value)


def _zip(name, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
    return buf.getvalue()


@respx.mock
def test_corp_codes_zip():
    xml = """<?xml version="1.0" encoding="UTF-8"?><result>
      <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code>
        <modify_date>20260101</modify_date></list>
      <list><corp_code>00999999</corp_code><corp_name>비상장사</corp_name><stock_code> </stock_code>
        <modify_date>20250101</modify_date></list></result>""".encode()
    respx.get(f"{BASE}/corpCode.xml").mock(return_value=httpx.Response(200, content=_zip("CORPCODE.xml", xml)))
    rows = adapter().fetch_corp_codes()
    assert rows[0] == {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930",
                       "modify_date": "20260101"}
    assert rows[1]["stock_code"] is None


@respx.mock
def test_document_non_zip_error():
    respx.get(f"{BASE}/document.xml").mock(return_value=httpx.Response(
        200, content="<result><status>014</status><message>파일 없음</message></result>".encode()))
    with pytest.raises(DartError, match="파일이 존재하지 않음"):
        adapter().fetch_document_files("20261110000001")
