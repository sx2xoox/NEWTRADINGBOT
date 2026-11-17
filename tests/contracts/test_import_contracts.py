"""설계 원칙(LLM 단독 주문 경로 금지, 예산 우회 금지)을 import 계약으로 검사."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_import_linter_contracts_hold():
    exe = Path(sys.executable).with_name("lint-imports")
    r = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_live_mode_is_refused_by_cli(monkeypatch, tmp_path):
    from kbot.app import cli
    from kbot.settings import Settings, TradingMode

    s = Settings(_env_file=None, trading_mode=TradingMode.LIVE, kbot_data_dir=tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    try:
        cli.main(["check"])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("live 모드가 거부되지 않았습니다")


def test_no_hardcoded_model_prices_outside_config():
    """단가는 config/llm_pricing.yaml에서만 관리 (코드에 하드코딩 금지)."""
    src = (ROOT / "src" / "kbot" / "llm" / "pricing.py").read_text(encoding="utf-8")
    for price in ("4.00", "20.00", "0.10", "0.50"):
        assert price not in src
