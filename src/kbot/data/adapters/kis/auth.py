"""KIS 접근토큰 발급·캐시.

공식 샘플(koreainvestment/open-trading-api, kis_auth.py) 기준:
- POST {domain}/oauth2/tokenP  body: grant_type, appkey, appsecret
- 응답: access_token, access_token_token_expired("YYYY-MM-DD HH:MM:SS")
- 유효기간 1일. 6시간 이내 재발급 요청 시 같은 토큰이 오고, 발급 시마다 알림톡이 가므로 파일에 캐시해 재사용.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from kbot.core import timeutil
from kbot.data.adapters.http import DataSourceError, HttpFetcher

PAPER_DOMAIN = "https://openapivts.koreainvestment.com:29443"
REAL_DOMAIN = "https://openapi.koreainvestment.com:9443"


class KisLiveNotSupported(RuntimeError):
    """실계좌 연결은 현재 단계에서 지원하지 않는다 (모의투자 전용)."""


@dataclass(frozen=True)
class KisCredentials:
    app_key: str
    app_secret: str
    paper: bool = True

    def __post_init__(self) -> None:
        if not self.paper:
            raise KisLiveNotSupported("실계좌(KIS 실전) 연결은 현재 지원하지 않습니다. 모의투자만 사용하세요.")
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_PAPER_APP_KEY / KIS_PAPER_APP_SECRET이 필요합니다")

    @property
    def domain(self) -> str:
        return PAPER_DOMAIN

    def __repr__(self) -> str:  # 비밀값 노출 방지
        return "KisCredentials(paper=True, app_key=***, app_secret=***)"


class KisTokenManager:
    def __init__(self, creds: KisCredentials, cache_path: Path, fetcher: HttpFetcher) -> None:
        self.creds = creds
        self.cache_path = cache_path
        self.http = fetcher
        self._token: str | None = None
        self._expires: datetime | None = None

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            exp = datetime.fromisoformat(data["expires"])
            if data.get("app_key_tail") == self.creds.app_key[-4:]:
                self._token, self._expires = data["token"], exp
        except (ValueError, KeyError, json.JSONDecodeError):
            self._token = self._expires = None

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"token": self._token, "expires": self._expires.isoformat() if self._expires else None,
                   "app_key_tail": self.creds.app_key[-4:]}
        # 소유자만 읽기/쓰기 (0600)
        fd = os.open(self.cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _valid(self) -> bool:
        # 만료 10분 전부터는 재발급
        return bool(self._token and self._expires and timeutil.now() < self._expires - timedelta(minutes=10))

    def token(self) -> str:
        if not self._valid():
            self._load_cache()
        if not self._valid():
            self._issue()
        assert self._token
        return self._token

    def _issue(self) -> None:
        resp = self.http.post(
            f"{self.creds.domain}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.creds.app_key,
                  "appsecret": self.creds.app_secret},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise DataSourceError(f"KIS 토큰 발급 실패 (HTTP {resp.status_code})")
        data = resp.json()
        self._token = data["access_token"]
        exp_local = datetime.strptime(data["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
        self._expires = exp_local.replace(tzinfo=timeutil.KST).astimezone(timeutil.UTC)
        self._save_cache()
