"""kbot 명령줄 도구.

  kbot init-db                       DB 생성
  kbot check                         설정·키 상태 점검 (값은 출력하지 않음)
  kbot budget                        이번 달 LLM 사용액·남은 예산
  kbot sync-reference                DART 고유번호·SEC 티커 매핑 갱신
  kbot ingest bars|fx|flows|disclosures [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from kbot.core import timeutil
from kbot.core.logging import setup_logging
from kbot.settings import Settings, TradingMode, get_settings
from kbot.storage.db import init_db, make_engine


def _engine(settings: Settings):  # noqa: ANN202
    engine = make_engine(settings.db_path)
    init_db(engine)
    return engine


def _guard_mode(settings: Settings) -> None:
    if settings.trading_mode == TradingMode.LIVE:
        print("TRADING_MODE=live 는 현재 지원하지 않습니다. 모의투자(paper)만 사용할 수 있습니다.", file=sys.stderr)
        sys.exit(2)


def cmd_check(settings: Settings) -> int:
    items = [
        ("실행 모드", settings.trading_mode.value),
        ("DB 경로", str(settings.db_path)),
        ("Anthropic API 키", "설정됨" if settings.anthropic_api_key else "없음"),
        ("월 LLM 예산", f"${settings.llm_monthly_budget_usd:.2f} (리셋일 매월 {settings.llm_budget_reset_day}일)"),
        ("KIS 모의투자 앱키", "설정됨" if settings.kis_paper_app_key and settings.kis_paper_app_secret else "없음"),
        ("DART API 키", "설정됨" if settings.dart_api_key else "없음"),
        ("SEC User-Agent", "설정됨" if settings.sec_user_agent else "없음"),
        ("ECOS API 키", "설정됨" if settings.ecos_api_key else "없음"),
        ("KRX 계정", "설정됨" if settings.krx_id and settings.krx_pw else "없음"),
        ("디스코드 웹훅", "설정됨" if settings.discord_webhook_url else "없음 (로그로만 알림)"),
    ]
    for k, v in items:
        print(f"{k:>16}: {v}")
    return 0


def cmd_budget(settings: Settings) -> int:
    from kbot.llm.factory import build_budget

    budget = build_budget(settings, _engine(settings))
    print(budget.status().summary_ko())
    return 0


def _build_sources(settings: Settings):  # noqa: ANN202
    from kbot.data.adapters.dart import DartAdapter
    from kbot.data.adapters.ecos import EcosAdapter
    from kbot.data.adapters.edgar import EdgarAdapter
    from kbot.data.adapters.kis.auth import KisCredentials
    from kbot.data.adapters.kis.quotations import KisQuotationAdapter
    from kbot.data.adapters.krx import PykrxAdapter
    from kbot.data.adapters.yf import YFinanceAdapter

    cfg = settings.load_yaml("settings.yaml")
    s: dict = {"bars": []}
    krx = PykrxAdapter(settings.krx_id.get_secret_value() if settings.krx_id else None,
                       settings.krx_pw.get_secret_value() if settings.krx_pw else None)
    s["krx"] = krx
    s["bars"].append(krx)
    if settings.kis_paper_app_key and settings.kis_paper_app_secret:
        creds = KisCredentials(settings.kis_paper_app_key.get_secret_value(),
                               settings.kis_paper_app_secret.get_secret_value(), paper=True)
        s["bars"].append(KisQuotationAdapter(
            creds, settings.kbot_data_dir / ".kis_token_paper.json",
            min_interval_sec=cfg.get("kis", {}).get("paper_min_interval_sec", 0.5)))
    s["bars"].append(YFinanceAdapter())
    s["dart"] = DartAdapter(settings.dart_api_key.get_secret_value()) if settings.dart_api_key else None
    s["edgar"] = EdgarAdapter(settings.sec_user_agent) if settings.sec_user_agent else None
    s["ecos"] = EcosAdapter(settings.ecos_api_key.get_secret_value()) if settings.ecos_api_key else None
    return s


def cmd_ingest(settings: Settings, what: str, start: date, end: date) -> int:
    from kbot.data.ingest.service import IngestService
    from kbot.data.universe import load_watchlist
    from kbot.storage.repositories import SqlDisclosureRepository, SqlMarketDataRepository

    engine = _engine(settings)
    svc = IngestService(SqlMarketDataRepository(engine), SqlDisclosureRepository(engine))
    src = _build_sources(settings)
    universe = load_watchlist(settings.load_yaml("universe.yaml"))
    if what == "bars":
        rep = svc.ingest_bars(universe, start, end, src["bars"])
    elif what == "fx":
        if not src["ecos"]:
            print("ECOS_API_KEY가 없어 환율을 수집할 수 없습니다.", file=sys.stderr)
            return 1
        rep = svc.ingest_fx(src["ecos"], start, end)
    elif what == "flows":
        rep = svc.ingest_flows(universe, src["krx"], start, end)
    elif what == "disclosures":
        rep = svc.ingest_disclosures(universe, start, end, dart=src["dart"], edgar=src["edgar"])
    elif what == "reference":
        rep = svc.sync_reference(src["dart"], src["edgar"])
    else:
        raise ValueError(what)
    print(rep.summary_ko())
    return 1 if rep.errors and not rep.saved else 0


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    setup_logging(settings.secret_values())
    _guard_mode(settings)

    p = argparse.ArgumentParser(prog="kbot", description="한국어 주식 분석·모의투자 봇")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="DB 생성")
    sub.add_parser("check", help="설정·키 상태 점검")
    sub.add_parser("budget", help="이번 달 LLM 사용액·남은 예산")
    sub.add_parser("sync-reference", help="DART 고유번호·SEC 티커 매핑 갱신")
    ing = sub.add_parser("ingest", help="데이터 수집")
    ing.add_argument("what", choices=["bars", "fx", "flows", "disclosures"])
    today = timeutil.today_kst()
    ing.add_argument("--start", type=date.fromisoformat, default=today - timedelta(days=30))
    ing.add_argument("--end", type=date.fromisoformat, default=today)
    args = p.parse_args(argv)

    if args.cmd == "init-db":
        _engine(settings)
        print(f"DB 준비 완료: {settings.db_path}")
        return 0
    if args.cmd == "check":
        return cmd_check(settings)
    if args.cmd == "budget":
        return cmd_budget(settings)
    if args.cmd == "sync-reference":
        return cmd_ingest(settings, "reference", today, today)
    if args.cmd == "ingest":
        return cmd_ingest(settings, args.what, args.start, args.end)
    return 1


if __name__ == "__main__":
    sys.exit(main())
