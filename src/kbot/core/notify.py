"""알림 인터페이스. 7단계에서 디스코드 봇으로 확장하며, 지금은 웹훅/로그 구현을 제공한다."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, title: str, body: str, level: str = "info") -> None: ...


class LogNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, title: str, body: str, level: str = "info") -> None:
        self.sent.append((title, body, level))
        log.log(logging.WARNING if level != "info" else logging.INFO, "[알림] %s — %s", title, body)


class DiscordWebhookNotifier:
    _ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        self._url = webhook_url
        self._timeout = timeout

    def send(self, title: str, body: str, level: str = "info") -> None:
        content = f"{self._ICON.get(level, '')} **{title}**\n{body}"[:1900]
        try:
            httpx.post(self._url, json={"content": content}, timeout=self._timeout).raise_for_status()
        except httpx.HTTPError as e:  # 알림 실패가 본 작업을 막지 않게 한다
            log.error("디스코드 알림 전송 실패: %s", type(e).__name__)
