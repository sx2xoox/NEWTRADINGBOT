"""공통 HTTP 유틸: 소스별 최소 호출 간격(레이트리밋) + 지수 백오프 재시도.

주의: 이 재시도는 '조회' 전용이다. 주문 전송은 멱등성 보장을 위해 6단계 OMS에서 별도로 처리하며
절대 이 헬퍼로 자동 재시도하지 않는다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    def __init__(self, min_interval_sec: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.min_interval = min_interval_sec
        self._sleep = sleep
        self._clock = clock
        self._last = -1e18
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = self._clock() - self._last
            if delta < self.min_interval:
                self._sleep(self.min_interval - delta)
            self._last = self._clock()


class DataSourceError(Exception):
    """데이터 소스 오류 (메시지는 한국어, 비밀값 미포함)."""


class HttpFetcher:
    def __init__(
        self,
        name: str,
        *,
        min_interval_sec: float = 0.2,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        client: httpx.Client | None = None,
        headers: dict[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_statuses: set[int] | frozenset[int] = frozenset(RETRY_STATUS),
    ) -> None:
        self.name = name
        self.limiter = RateLimiter(min_interval_sec, sleep=sleep)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.retry_statuses = set(retry_statuses)
        self._sleep = sleep
        self.client = client or httpx.Client(timeout=30.0, headers=headers or {}, follow_redirects=True)
        if client is not None and headers:
            self.client.headers.update(headers)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_exc = e
                log.warning("[%s] 연결 오류(%s), 재시도 %d/%d", self.name, type(e).__name__,
                            attempt + 1, self.max_retries)
            else:
                if resp.status_code not in self.retry_statuses:
                    return resp
                last_exc = DataSourceError(f"[{self.name}] HTTP {resp.status_code}")
                log.warning("[%s] HTTP %d, 재시도 %d/%d", self.name, resp.status_code,
                            attempt + 1, self.max_retries)
                ra = resp.headers.get("retry-after")
                if ra and ra.isdigit() and attempt < self.max_retries:
                    self._sleep(min(float(ra), 60.0))
                    continue
            if attempt < self.max_retries:
                self._sleep(self.backoff_base * (2 ** attempt))
        raise DataSourceError(f"[{self.name}] 요청 실패: {type(last_exc).__name__}") from last_exc

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)
