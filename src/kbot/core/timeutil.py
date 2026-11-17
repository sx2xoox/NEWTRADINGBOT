"""시간 유틸리티. 모든 '현재 시각'은 이 모듈의 now()를 거쳐 테스트에서 고정할 수 있게 한다."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
UTC = UTC


def now() -> datetime:
    """현재 시각 (UTC, tz-aware)."""
    return datetime.now(UTC)


def now_kst() -> datetime:
    return now().astimezone(KST)


def today_kst() -> date:
    return now_kst().date()
