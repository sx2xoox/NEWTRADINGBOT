"""관심 종목/LLM 분석 범위 로더."""

from __future__ import annotations

from typing import Any

from kbot.core.types import Instrument, Market


def load_watchlist(cfg: dict[str, Any]) -> list[Instrument]:
    return [Instrument(symbol=str(x["symbol"]), market=Market(x["market"]), name=x.get("name", ""))
            for x in cfg.get("watchlist", [])]
