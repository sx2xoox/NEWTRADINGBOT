"""SQLite 스키마와 엔진. 저장소(repository) 계층만 이 모듈을 직접 사용한다.

SQLAlchemy Core를 사용하므로 나중에 Postgres 등으로 옮길 때 엔진 URL만 바꾸면 된다.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

daily_bars = Table(
    "daily_bars", metadata,
    Column("market", String(10), primary_key=True),
    Column("symbol", String(20), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("adjusted", Boolean, primary_key=True),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=False),
    Column("value", Float),
    Column("source", String(20), nullable=False),
)

fx_rates = Table(
    "fx_rates", metadata,
    Column("pair", String(10), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("rate", Float, nullable=False),
    Column("source", String(20), nullable=False),
)

investor_flows = Table(
    "investor_flows", metadata,
    Column("symbol", String(20), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("foreign_net", Float, nullable=False),
    Column("institution_net", Float, nullable=False),
    Column("individual_net", Float, nullable=False),
    Column("source", String(20), nullable=False),
)

disclosures = Table(
    "disclosures", metadata,
    Column("source", String(10), primary_key=True),
    Column("doc_id", String(40), primary_key=True),
    Column("symbol", String(20), index=True),
    Column("company", String(200), nullable=False),
    Column("title", String(500), nullable=False),
    Column("filed_on", Date, nullable=False, index=True),
    Column("url", String(500), nullable=False),
    Column("form_type", String(40), nullable=False, default=""),
    Column("is_correction", Boolean, nullable=False, default=False),
    Column("extra", Text, nullable=False, default=""),
    Column("raw_text", Text),            # 원문 (3단계에서 채움)
)

# 상장폐지 종목 포함 종목 마스터 (생존 편향 방지)
listings = Table(
    "listings", metadata,
    Column("market", String(10), primary_key=True),
    Column("symbol", String(20), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("listed_on", Date),
    Column("delisted_on", Date),
    Column("delist_reason", String(200)),
    Column("source", String(20), nullable=False),
)

dart_corp_codes = Table(
    "dart_corp_codes", metadata,
    Column("corp_code", String(8), primary_key=True),
    Column("corp_name", String(200), nullable=False),
    Column("stock_code", String(6), index=True),
    Column("modify_date", String(8)),
)

sec_tickers = Table(
    "sec_tickers", metadata,
    Column("ticker", String(20), primary_key=True),
    Column("cik", Integer, nullable=False, index=True),
    Column("title", String(300), nullable=False),
)

# ── LLM 예산 원장 ─────────────────────────────────────────────
llm_usage = Table(
    "llm_usage", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("period_start", Date, nullable=False, index=True),
    Column("day_kst", Date, nullable=False, index=True),
    Column("feature", String(50), nullable=False),
    Column("model", String(50), nullable=False),
    Column("batch", Boolean, nullable=False, default=False),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("cache_write_5m_tokens", Integer, nullable=False, default=0),
    Column("cache_write_1h_tokens", Integer, nullable=False, default=0),
    Column("cache_read_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("cost_usd", Float, nullable=False),
    Column("request_id", String(100)),
    Column("note", String(200), nullable=False, default=""),
)

llm_reservations = Table(
    "llm_reservations", metadata,
    Column("id", String(36), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("period_start", Date, nullable=False, index=True),
    Column("day_kst", Date, nullable=False, index=True),
    Column("feature", String(50), nullable=False),
    Column("amount_usd", Float, nullable=False),
    Column("status", String(10), nullable=False, index=True),  # active | settled | released
)

llm_budget_alerts = Table(
    "llm_budget_alerts", metadata,
    Column("period_start", Date, primary_key=True),
    Column("level", String(20), primary_key=True),
    Column("sent_at", DateTime(timezone=True), nullable=False),
)

llm_cache = Table(
    "llm_cache", metadata,
    Column("cache_key", String(64), primary_key=True),
    Column("feature", String(50), nullable=False),
    Column("model", String(50), nullable=False),
    Column("prompt_version", String(20), nullable=False),
    Column("response_text", Text, nullable=False),
    Column("cost_usd", Float, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("cache_key"),
)


def make_engine(db_path: Path | str) -> Engine:
    url = "sqlite+pysqlite:///:memory:" if str(db_path) == ":memory:" else f"sqlite+pysqlite:///{db_path}"
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
