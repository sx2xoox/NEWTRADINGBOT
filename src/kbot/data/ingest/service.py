"""수집 서비스: 어댑터에서 받아 저장소에 넣는다. 소스 실패 시 다음 소스로 넘어간다."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from kbot.core.types import Instrument
from kbot.data.adapters.base import DailyBarSource, FxSource, InvestorFlowSource
from kbot.data.adapters.dart import DartAdapter
from kbot.data.adapters.edgar import EdgarAdapter
from kbot.data.adapters.http import DataSourceError
from kbot.storage.repositories import SqlDisclosureRepository, SqlMarketDataRepository

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    saved: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add(self, key: str, n: int) -> None:
        self.saved[key] = self.saved.get(key, 0) + n

    def summary_ko(self) -> str:
        lines = [f"- {k}: {v}건 저장" for k, v in self.saved.items()] or ["- 저장된 데이터 없음"]
        if self.errors:
            lines.append(f"- 오류 {len(self.errors)}건:")
            lines += [f"  · {e}" for e in self.errors]
        return "\n".join(lines)


class IngestService:
    def __init__(self, market_repo: SqlMarketDataRepository, disclosure_repo: SqlDisclosureRepository) -> None:
        self.market = market_repo
        self.disc = disclosure_repo

    def ingest_bars(self, instruments: Sequence[Instrument], start: date, end: date,
                    sources: Sequence[DailyBarSource]) -> IngestReport:
        rep = IngestReport()
        for inst in instruments:
            candidates = [s for s in sources if s.supports(inst.market)]
            if not candidates:
                rep.errors.append(f"{inst.key}: 사용할 수 있는 시세 소스 없음")
                continue
            for src in candidates:
                try:
                    bars = src.fetch_daily_bars(inst.market, inst.symbol, start, end)
                except (DataSourceError, ValueError, KeyError) as e:
                    log.warning("%s %s 실패: %s", src.name, inst.key, e)
                    rep.errors.append(f"{inst.key} ({src.name}): {e}")
                    continue
                rep.add(f"일봉[{src.name}]", self.market.upsert_bars(bars))
                break
        return rep

    def ingest_fx(self, src: FxSource, start: date, end: date) -> IngestReport:
        rep = IngestReport()
        try:
            rep.add(f"환율[{src.name}]", self.market.upsert_fx(src.fetch_usdkrw(start, end)))
        except DataSourceError as e:
            rep.errors.append(str(e))
        return rep

    def ingest_flows(self, instruments: Sequence[Instrument], src: InvestorFlowSource,
                     start: date, end: date) -> IngestReport:
        rep = IngestReport()
        for inst in instruments:
            if not inst.market.is_domestic:
                continue
            try:
                rep.add(f"수급[{src.name}]", self.market.upsert_flows(src.fetch_flows(inst.symbol, start, end)))
            except (DataSourceError, ValueError, KeyError) as e:
                rep.errors.append(f"{inst.key}: {e}")
        return rep

    def sync_reference(self, dart: DartAdapter | None, edgar: EdgarAdapter | None) -> IngestReport:
        rep = IngestReport()
        if dart:
            try:
                rep.add("DART 고유번호", self.disc.upsert_dart_corp_codes(dart.fetch_corp_codes()))
            except DataSourceError as e:
                rep.errors.append(str(e))
        if edgar:
            try:
                rep.add("SEC 티커", self.disc.upsert_sec_tickers(edgar.fetch_ticker_map()))
            except DataSourceError as e:
                rep.errors.append(str(e))
        return rep

    def ingest_disclosures(self, instruments: Sequence[Instrument], start: date, end: date, *,
                           dart: DartAdapter | None, edgar: EdgarAdapter | None) -> IngestReport:
        rep = IngestReport()
        for inst in instruments:
            try:
                if inst.market.is_domestic:
                    if dart is None:
                        continue
                    corp = self.disc.dart_corp_code_for(inst.symbol)
                    if not corp:
                        rep.errors.append(f"{inst.key}: DART 고유번호 없음 (sync-reference 먼저 실행)")
                        continue
                    items = dart.fetch_disclosures(start, end, corp_code=corp)
                    rep.add("공시[DART]", self.disc.upsert_disclosures(items))
                else:
                    if edgar is None:
                        continue
                    cik = self.disc.cik_for(inst.symbol)
                    if not cik:
                        rep.errors.append(f"{inst.key}: SEC CIK 없음 (sync-reference 먼저 실행)")
                        continue
                    items = edgar.fetch_filings(cik, start, end, ticker=inst.symbol)
                    rep.add("공시[EDGAR]", self.disc.upsert_disclosures(items))
            except DataSourceError as e:
                rep.errors.append(f"{inst.key}: {e}")
        return rep
