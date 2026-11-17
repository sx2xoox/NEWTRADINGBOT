from datetime import date

import httpx
import pytest
import respx

from kbot.data.adapters.ecos import BASE as ECOS_BASE
from kbot.data.adapters.ecos import EcosAdapter
from kbot.data.adapters.edgar import SUBMISSIONS_URL, TICKERS_URL, EdgarAdapter
from kbot.data.adapters.http import HttpFetcher

UA = "EM test@example.com"


def edgar():
    return EdgarAdapter(UA, fetcher=HttpFetcher("EDGAR", min_interval_sec=0, sleep=lambda s: None))


def test_user_agent_required():
    with pytest.raises(ValueError):
        EdgarAdapter("")
    with pytest.raises(ValueError):
        EdgarAdapter("no email here")


@respx.mock
def test_ticker_map_and_user_agent_header():
    route = respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json={
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}))
    assert edgar().fetch_ticker_map() == [{"ticker": "NVDA", "cik": 1045810, "title": "NVIDIA CORP"}]
    assert route.calls[0].request.headers["User-Agent"] == UA


@respx.mock
def test_filings_filtered_by_date_and_form():
    recent = {
        "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003", "0001-26-000004"],
        "filingDate": ["2026-11-10", "2026-11-09", "2026-10-01", "2026-11-12"],
        "form": ["8-K", "4", "10-Q", "10-Q/A"],
        "items": ["2.02,9.01", "", "", ""],
        "primaryDocument": ["a.htm", "b.xml", "c.htm", "d.htm"],
        "acceptanceDateTime": ["", "", "", ""], "reportDate": ["", "", "", ""],
    }
    respx.get(SUBMISSIONS_URL.format(cik=1045810)).mock(return_value=httpx.Response(
        200, json={"name": "NVIDIA CORP", "filings": {"recent": recent}}))
    out = edgar().fetch_filings(1045810, date(2026, 11, 1), date(2026, 11, 17), ticker="nvda")
    assert [d.form_type for d in out] == ["8-K", "10-Q/A"]
    assert out[0].title == "8-K (Items 2.02,9.01)" and out[0].symbol == "NVDA"
    assert out[0].url == "https://www.sec.gov/Archives/edgar/data/1045810/000126000001/a.htm"
    assert out[1].is_correction


def ecos():
    return EcosAdapter("ECOSKEY", fetcher=HttpFetcher("ECOS", min_interval_sec=0, sleep=lambda s: None))


@respx.mock
def test_ecos_usdkrw_parse():
    respx.get(url__startswith=ECOS_BASE).mock(return_value=httpx.Response(200, json={
        "StatisticSearch": {"list_total_count": 2, "row": [
            {"TIME": "20251103", "DATA_VALUE": "1427.4"}, {"TIME": "20251104", "DATA_VALUE": "1429"}]}}))
    out = ecos().fetch_usdkrw(date(2025, 11, 1), date(2025, 11, 5))
    assert [(r.day, r.rate) for r in out] == [(date(2025, 11, 3), 1427.4), (date(2025, 11, 4), 1429.0)]


@respx.mock
def test_ecos_no_data():
    respx.get(url__startswith=ECOS_BASE).mock(return_value=httpx.Response(200, json={
        "RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}))
    assert ecos().fetch_usdkrw(date(2025, 11, 1), date(2025, 11, 2)) == []
