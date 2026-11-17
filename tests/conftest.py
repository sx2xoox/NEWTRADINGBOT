from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kbot.core.notify import LogNotifier
from kbot.llm.budget import BudgetManager
from kbot.llm.pricing import PriceTable
from kbot.storage.db import init_db, make_engine

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(tmp_path / "test.db")
    init_db(eng)
    return eng


@pytest.fixture
def prices() -> PriceTable:
    return PriceTable.from_config(yaml.safe_load((CONFIG / "llm_pricing.yaml").read_text(encoding="utf-8")))


@pytest.fixture
def models_cfg() -> dict:
    return yaml.safe_load((CONFIG / "llm_models.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def notifier() -> LogNotifier:
    return LogNotifier()


@pytest.fixture
def make_budget(engine, prices, notifier):
    def _make(monthly: float = 150.0, reset_day: int = 1) -> BudgetManager:
        return BudgetManager(engine, prices, monthly, reset_day, notifier=notifier)

    return _make
