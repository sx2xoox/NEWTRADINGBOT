"""저장소 계층. 상위 계층은 여기의 인터페이스(Protocol)만 의존한다."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import date
from typing import Protocol

from sqlalchemy import and_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from kbot.core.types import DailyBar, Disclosure, FxRate, InvestorFlow, Market
from kbot.storage import db


# ── 인터페이스 ────────────────────────────────────────────────
class BarRepository(Protocol):
    def upsert_bars(self, bars: Iterable[DailyBar]) -> int: ...
    def get_bars(self, market: Market, symbol: str, start: date, end: date,
                 adjusted: bool = True) -> list[DailyBar]: ...


class DisclosureRepository(Protocol):
    def upsert_disclosures(self, items: Iterable[Disclosure]) -> int: ...
    def get_disclosure(self, source: str, doc_id: str) -> Disclosure | None: ...
    def list_disclosures(self, start: date, end: date, symbol: str | None = None) -> list[Disclosure]: ...


# ── SQLite 구현 ───────────────────────────────────────────────
def _upsert(engine: Engine, table, rows: Sequence[dict], key_cols: Sequence[str]) -> int:
    if not rows:
        return 0
    stmt = sqlite_insert(table)
    # 행에 실제로 들어 있는 컬럼만 갱신 (예: 메타데이터 재수집이 저장된 원문(raw_text)을 지우지 않도록)
    present = set(rows[0])
    update_cols = {c.name: stmt.excluded[c.name] for c in table.columns
                   if c.name not in key_cols and c.name in present}
    stmt = stmt.on_conflict_do_update(index_elements=list(key_cols), set_=update_cols)
    with engine.begin() as conn:
        conn.execute(stmt, list(rows))
    return len(rows)


class SqlMarketDataRepository:
    """시세·환율·수급 저장소."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # 일봉
    def upsert_bars(self, bars: Iterable[DailyBar]) -> int:
        rows = []
        for b in bars:
            d = asdict(b)
            d["market"] = b.market.value
            rows.append(d)
        return _upsert(self.engine, db.daily_bars, rows, ["market", "symbol", "day", "adjusted"])

    def get_bars(self, market: Market, symbol: str, start: date, end: date,
                 adjusted: bool = True) -> list[DailyBar]:
        t = db.daily_bars
        q = (select(t).where(and_(t.c.market == market.value, t.c.symbol == symbol,
                                  t.c.day >= start, t.c.day <= end, t.c.adjusted == adjusted))
             .order_by(t.c.day))
        with self.engine.connect() as conn:
            return [DailyBar(**{**r._mapping, "market": Market(r.market)}) for r in conn.execute(q)]

    # 환율
    def upsert_fx(self, rates: Iterable[FxRate]) -> int:
        return _upsert(self.engine, db.fx_rates, [asdict(r) for r in rates], ["pair", "day"])

    def get_fx(self, pair: str, start: date, end: date) -> list[FxRate]:
        t = db.fx_rates
        q = select(t).where(and_(t.c.pair == pair, t.c.day >= start, t.c.day <= end)).order_by(t.c.day)
        with self.engine.connect() as conn:
            return [FxRate(**r._mapping) for r in conn.execute(q)]

    # 투자자별 수급
    def upsert_flows(self, flows: Iterable[InvestorFlow]) -> int:
        return _upsert(self.engine, db.investor_flows, [asdict(f) for f in flows], ["symbol", "day"])

    def get_flows(self, symbol: str, start: date, end: date) -> list[InvestorFlow]:
        t = db.investor_flows
        q = select(t).where(and_(t.c.symbol == symbol, t.c.day >= start, t.c.day <= end)).order_by(t.c.day)
        with self.engine.connect() as conn:
            return [InvestorFlow(**r._mapping) for r in conn.execute(q)]

    # 종목 마스터 (상장폐지 포함)
    def upsert_listings(self, rows: Iterable[dict]) -> int:
        return _upsert(self.engine, db.listings, list(rows), ["market", "symbol"])


class SqlDisclosureRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert_disclosures(self, items: Iterable[Disclosure]) -> int:
        # raw_text는 별도 메서드로만 갱신 (메타데이터 재수집 시 원문을 지우지 않도록)
        return _upsert(self.engine, db.disclosures, [asdict(d) for d in items], ["source", "doc_id"])

    def set_raw_text(self, source: str, doc_id: str, text: str) -> None:
        t = db.disclosures
        with self.engine.begin() as conn:
            conn.execute(t.update().where(and_(t.c.source == source, t.c.doc_id == doc_id))
                         .values(raw_text=text))

    def get_raw_text(self, source: str, doc_id: str) -> str | None:
        t = db.disclosures
        with self.engine.connect() as conn:
            return conn.execute(select(t.c.raw_text).where(
                and_(t.c.source == source, t.c.doc_id == doc_id))).scalar_one_or_none()

    @staticmethod
    def _row(r) -> Disclosure:  # noqa: ANN001
        m = dict(r._mapping)
        m.pop("raw_text", None)
        return Disclosure(**m)

    def get_disclosure(self, source: str, doc_id: str) -> Disclosure | None:
        t = db.disclosures
        with self.engine.connect() as conn:
            r = conn.execute(select(t).where(and_(t.c.source == source, t.c.doc_id == doc_id))).first()
        return self._row(r) if r else None

    def list_disclosures(self, start: date, end: date, symbol: str | None = None) -> list[Disclosure]:
        t = db.disclosures
        cond = [t.c.filed_on >= start, t.c.filed_on <= end]
        if symbol:
            cond.append(t.c.symbol == symbol)
        with self.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(select(t).where(and_(*cond)).order_by(t.c.filed_on))]

    # 코드 매핑
    def upsert_dart_corp_codes(self, rows: Iterable[dict]) -> int:
        return _upsert(self.engine, db.dart_corp_codes, list(rows), ["corp_code"])

    def dart_corp_code_for(self, stock_code: str) -> str | None:
        t = db.dart_corp_codes
        with self.engine.connect() as conn:
            return conn.execute(select(t.c.corp_code).where(t.c.stock_code == stock_code)).scalars().first()

    def upsert_sec_tickers(self, rows: Iterable[dict]) -> int:
        return _upsert(self.engine, db.sec_tickers, list(rows), ["ticker"])

    def cik_for(self, ticker: str) -> int | None:
        t = db.sec_tickers
        with self.engine.connect() as conn:
            return conn.execute(select(t.c.cik).where(t.c.ticker == ticker.upper())).scalar_one_or_none()


def dumps_extra(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, sort_keys=True)
