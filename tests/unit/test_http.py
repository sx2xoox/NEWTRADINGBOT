import httpx
import pytest
import respx

from kbot.data.adapters.http import DataSourceError, HttpFetcher, RateLimiter


@respx.mock
def test_retries_on_503_then_succeeds():
    route = respx.get("https://x.test/a").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
    sleeps = []
    f = HttpFetcher("T", min_interval_sec=0, sleep=sleeps.append)
    assert f.get("https://x.test/a").status_code == 200
    assert route.call_count == 2 and sleeps  # 백오프 수행


@respx.mock
def test_gives_up_after_max_retries():
    respx.get("https://x.test/a").mock(return_value=httpx.Response(500))
    f = HttpFetcher("T", min_interval_sec=0, max_retries=2, sleep=lambda s: None)
    with pytest.raises(DataSourceError):
        f.get("https://x.test/a")


@respx.mock
def test_does_not_retry_4xx():
    route = respx.get("https://x.test/a").mock(return_value=httpx.Response(404))
    f = HttpFetcher("T", min_interval_sec=0, sleep=lambda s: None)
    assert f.get("https://x.test/a").status_code == 404 and route.call_count == 1


def test_rate_limiter_enforces_interval():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    rl = RateLimiter(0.5, sleep=sleep, clock=lambda: t[0])
    rl.wait()
    rl.wait()
    assert slept == [pytest.approx(0.5)]
