from datetime import date

from kbot.core.types import DailyBar, Disclosure, Instrument, Market
from kbot.data.adapters.http import DataSourceError
from kbot.data.ingest.service import IngestService
from kbot.storage.repositories import SqlDisclosureRepository, SqlMarketDataRepository


class Src:
    def __init__(self, name, ok=True, domestic=True):
        self.name, self.ok, self.domestic, self.calls = name, ok, domestic, 0

    def supports(self, m):
        return m.is_domestic == self.domestic

    def fetch_daily_bars(self, market, symbol, start, end):
        self.calls += 1
        if not self.ok:
            raise DataSourceError("실패")
        return [DailyBar(symbol, market, start, 1, 2, 0.5, 1.5, 100, source=self.name)]


def test_bar_source_fallback(engine):
    repo = SqlMarketDataRepository(engine)
    svc = IngestService(repo, SqlDisclosureRepository(engine))
    bad, good = Src("A", ok=False), Src("B")
    rep = svc.ingest_bars([Instrument("005930", Market.KOSPI)], date(2026, 11, 3), date(2026, 11, 3), [bad, good])
    assert bad.calls == 1 and good.calls == 1
    assert rep.saved == {"일봉[B]": 1} and len(rep.errors) == 1
    bars = repo.get_bars(Market.KOSPI, "005930", date(2026, 11, 1), date(2026, 11, 30))
    assert bars[0].source == "B"


def test_upsert_is_idempotent(engine):
    repo = SqlMarketDataRepository(engine)
    b = DailyBar("NVDA", Market.NASDAQ, date(2026, 11, 3), 1, 2, 0.5, 1.5, 100, source="X")
    repo.upsert_bars([b])
    repo.upsert_bars([DailyBar("NVDA", Market.NASDAQ, date(2026, 11, 3), 1, 2, 0.5, 1.7, 100, source="X")])
    bars = repo.get_bars(Market.NASDAQ, "NVDA", date(2026, 11, 1), date(2026, 11, 30))
    assert len(bars) == 1 and bars[0].close == 1.7


def test_disclosure_raw_text_preserved_on_metadata_reupsert(engine):
    repo = SqlDisclosureRepository(engine)
    d = Disclosure("DART", "20261110000001", "005930", "삼성전자", "유상증자결정", date(2026, 11, 10), "u")
    repo.upsert_disclosures([d])
    repo.set_raw_text("DART", d.doc_id, "원문")
    repo.upsert_disclosures([d])
    assert repo.get_raw_text("DART", d.doc_id) == "원문"
    assert repo.get_disclosure("DART", d.doc_id) == d


def test_disclosure_ingest_requires_reference(engine):
    svc = IngestService(SqlMarketDataRepository(engine), SqlDisclosureRepository(engine))

    class FakeDart:
        def fetch_disclosures(self, *a, **k):
            raise AssertionError("호출되면 안 됨")

    rep = svc.ingest_disclosures([Instrument("005930", Market.KOSPI)], date(2026, 11, 1), date(2026, 11, 2),
                                 dart=FakeDart(), edgar=None)
    assert "sync-reference" in rep.errors[0]
