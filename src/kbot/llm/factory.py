"""설정으로부터 예산 관리자·라우터·클라이언트를 조립."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from kbot.core.notify import DiscordWebhookNotifier, LogNotifier, Notifier
from kbot.llm.budget import BudgetManager
from kbot.llm.pricing import PriceTable
from kbot.llm.router import ModelRouter
from kbot.settings import Settings


def build_notifier(settings: Settings) -> Notifier:
    if settings.discord_webhook_url:
        return DiscordWebhookNotifier(settings.discord_webhook_url.get_secret_value())
    return LogNotifier()


def build_budget(settings: Settings, engine: Engine, notifier: Notifier | None = None) -> BudgetManager:
    prices = PriceTable.from_config(settings.load_yaml("llm_pricing.yaml"))
    llm_cfg = settings.load_yaml("settings.yaml").get("llm", {})
    return BudgetManager(
        engine, prices, settings.llm_monthly_budget_usd, settings.llm_budget_reset_day,
        warn_ratio=llm_cfg.get("warn_ratio", 0.70), degrade_ratio=llm_cfg.get("degrade_ratio", 0.90),
        notifier=notifier or build_notifier(settings),
    )


def build_llm_client(settings: Settings, engine: Engine):  # noqa: ANN201
    from kbot.llm.client import LLMClient  # anthropic import는 여기서만 지연 로드

    budget = build_budget(settings, engine)
    router = ModelRouter(settings.load_yaml("llm_models.yaml"), budget.prices)
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    return LLMClient(engine, budget, router, api_key=key, secrets=settings.secret_values())
