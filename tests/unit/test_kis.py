import json
import os
import stat
from datetime import date, datetime

import httpx
import pytest
import respx
import time_machine

from kbot.core.timeutil import KST
from kbot.core.types import Market
from kbot.data.adapters.http import HttpFetcher
from kbot.data.adapters.kis.auth import PAPER_DOMAIN, KisCredentials, KisLiveNotSupported
from kbot.data.adapters.kis.quotations import DOMESTIC_DAILY, OVERSEAS_DAILY, KisError, KisQuotationAdapter

NOW = datetime(2026, 11, 17, 9, tzinfo=KST)
TOKEN_RESP = {"access_token": "TOKEN123", "access_token_token_expired": "2026-11-18 09:00:00",
              "token_type": "Bearer", "expires_in": 86400}


def creds():
    return KisCredentials("APPKEY0001", "APPSECRET0001", paper=True)


def adapter(tmp_path):
    return KisQuotationAdapter(creds(), tmp_path / "tok.json",
                               fetcher=HttpFetcher("KIS", min_interval_sec=0, sleep=lambda s: None),
                               sleep=lambda s: None)


def test_live_mode_rejected():
    with pytest.raises(KisLiveNotSupported):
        KisCredentials("a", "b", paper=False)


def test_repr_hides_secrets():
    assert "APPSECRET0001" not in repr(creds()) and "APPKEY0001" not in repr(creds())


@respx.mock
@time_machine.travel(NOW, tick=False)
def test_token_cached_to_file_with_0600_and_reused(tmp_path):
    tok = respx.post(f"{PAPER_DOMAIN}/oauth2/tokenP").mock(return_value=httpx.Response(200, json=TOKEN_RESP))
    a = adapter(tmp_path)
    assert a.tokens.token() == "TOKEN123"
    assert a.tokens.token() == "TOKEN123"
    assert tok.call_count == 1
    mode = stat.S_IMODE(os.stat(tmp_path / "tok.json").st_mode)
    assert mode == 0o600
    # 새 프로세스(새 어댑터)도 파일 캐시를 재사용 → 알림톡 재발송 없음
    assert adapter(tmp_path).tokens.token() == "TOKEN123" and tok.call_count == 1
    assert json.loads(body := tok.calls[0].request.content)["appsecret"] == "APPSECRET0001" and body


@respx.mock
def test_expired_token_reissued(tmp_path):
    tok = respx.post(f"{PAPER_DOMAIN}/oauth2/tokenP").mock(return_value=httpx.Response(200, json=TOKEN_RESP))
    with time_machine.travel(NOW, tick=False):
        adapter(tmp_path).tokens.token()
    with time_machine.travel(datetime(2026, 11, 18, 8, 55, tzinfo=KST), tick=False):  # 만료 10분 이내
        adapter(tmp_path).tokens.token()
    assert tok.call_count == 2


def _dom_row(d, c):
    return {"stck_bsop_date": d, "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90",
            "stck_clpr": str(c), "acml_vol": "1000", "acml_tr_pbmn": "100000"}


@respx.mock
@time_machine.travel(NOW, tick=False)
def test_domestic_daily_parse_headers_and_chunking(tmp_path):
    respx.post(f"{PAPER_DOMAIN}/oauth2/tokenP").mock(return_value=httpx.Response(200, json=TOKEN_RESP))
    route = respx.get(f"{PAPER_DOMAIN}{DOMESTIC_DAILY}").mock(return_value=httpx.Response(200, json={
        "rt_cd": "0", "output1": {}, "output2": [_dom_row("20261116", 105), _dom_row("20261113", 101), {}]}))
    bars = adapter(tmp_path).fetch_daily_bars(Market.KOSPI, "005930", date(2026, 3, 1), date(2026, 11, 16))
    assert route.call_count == 2          # 260일 → 130일 단위 2회
    assert [(b.day, b.close) for b in bars] == [(date(2026, 11, 13), 101.0), (date(2026, 11, 16), 105.0)]
    h = route.calls[0].request.headers
    assert h["tr_id"] == "FHKST03010100" and h["authorization"] == "Bearer TOKEN123" and h["custtype"] == "P"
    assert route.calls[0].request.url.params["FID_ORG_ADJ_PRC"] == "0"


@respx.mock
@time_machine.travel(NOW, tick=False)
def test_rate_limit_code_retried(tmp_path):
    respx.post(f"{PAPER_DOMAIN}/oauth2/tokenP").mock(return_value=httpx.Response(200, json=TOKEN_RESP))
    route = respx.get(f"{PAPER_DOMAIN}{DOMESTIC_DAILY}").mock(side_effect=[
        httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}),
        httpx.Response(200, json={"rt_cd": "0", "output2": [_dom_row("20261116", 1)]}),
    ])
    a = KisQuotationAdapter(creds(), tmp_path / "t.json",
                            fetcher=HttpFetcher("KIS", min_interval_sec=0, max_retries=0, sleep=lambda s: None,
                                                retry_statuses={502, 503, 504}),
                            sleep=lambda s: None)
    assert len(a.fetch_daily_bars(Market.KOSPI, "005930", date(2026, 11, 16), date(2026, 11, 16))) == 1
    assert route.call_count == 2


@respx.mock
@time_machine.travel(NOW, tick=False)
def test_error_message_surfaces(tmp_path):
    respx.post(f"{PAPER_DOMAIN}/oauth2/tokenP").mock(return_value=httpx.Response(200, json=TOKEN_RESP))
    respx.get(f"{PAPER_DOMAIN}{DOMESTIC_DAILY}").mock(return_value=httpx.Response(
        200, json={"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "조회 오류"}))
    with pytest.raises(KisError, match="OPSQ0001"):
        adapter(tmp_path).fetch_daily_bars(Market.KOSPI, "005930", date(2026, 11, 16), date(2026, 11, 16))


@respx.mock
@time_machine.travel(NOW, tick=False)
def test_overseas_pages_backwards_by_bymd(tmp_path):
    respx.post(f"{PAPER_DOMAIN}/oauth2/tokenP").mock(return_value=httpx.Response(200, json=TOKEN_RESP))

    def row(d, c):
        return {"xymd": d, "open": "1", "high": "2", "low": "0.5", "clos": str(c), "tvol": "10", "tamt": "100"}

    route = respx.get(f"{PAPER_DOMAIN}{OVERSEAS_DAILY}").mock(side_effect=[
        httpx.Response(200, json={"rt_cd": "0", "output2": [row("20261114", 3), row("20261113", 2)]}),
        httpx.Response(200, json={"rt_cd": "0", "output2": [row("20261112", 1), row("20261111", 0.9)]}),
    ])
    bars = adapter(tmp_path).fetch_daily_bars(Market.NASDAQ, "NVDA", date(2026, 11, 12), date(2026, 11, 14))
    assert [b.close for b in bars] == [1.0, 2.0, 3.0]         # 기간 밖(11/11) 제외, 오름차순
    p1, p2 = (c.request.url.params for c in route.calls)
    assert p1["EXCD"] == "NAS" and p1["BYMD"] == "20261114" and p2["BYMD"] == "20261112"
    assert route.calls[0].request.headers["tr_id"] == "HHDFS76240000"
