"""시크릿 마스킹 로거.

로그 레코드의 메시지·인자에 등록된 비밀값이 나타나면 '***'로 치환한다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

_MASK = "***"
# 비밀값이 등록되지 않았더라도 흔한 패턴은 가린다.
_GENERIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), _MASK),
    (re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?bearer\s+)[A-Za-z0-9._\-]+"), r"\g<1>" + _MASK),
    (re.compile(r"(?i)((?:appsecret|appkey|crtfc_key|api_key|apikey)\"?\s*[:=]\s*\"?)[A-Za-z0-9+/=._\-]{6,}"),
     r"\g<1>" + _MASK),
]


class SecretMaskingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def add_secrets(self, secrets: Iterable[str]) -> None:
        merged = set(self._secrets) | {s for s in secrets if s and len(s) >= 4}
        self._secrets = sorted(merged, key=len, reverse=True)

    def mask(self, text: str) -> str:
        for s in self._secrets:
            if s in text:
                text = text.replace(s, _MASK)
        for pat, repl in _GENERIC_PATTERNS:
            text = pat.sub(repl, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — 포맷 오류여도 로그는 남긴다
            msg = str(record.msg)
        record.msg = self.mask(msg)
        record.args = None
        return True


_filter = SecretMaskingFilter()


def setup_logging(secrets: Iterable[str] = (), level: int = logging.INFO) -> SecretMaskingFilter:
    _filter.add_secrets(secrets)
    root = logging.getLogger()
    if not any(isinstance(f, SecretMaskingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(_filter)
        root.addHandler(handler)
    root.setLevel(level)
    return _filter


def get_masking_filter() -> SecretMaskingFilter:
    return _filter
