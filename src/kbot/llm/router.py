"""작업 등급 → 모델 라우팅. 예산 상태에 따라 강등한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kbot.llm.budget import BudgetLevel
from kbot.llm.errors import BudgetExhausted
from kbot.llm.pricing import PriceTable


@dataclass(frozen=True)
class Route:
    tier: str
    model: str
    effort: str
    degraded: bool = False


class ModelRouter:
    def __init__(self, cfg: dict[str, Any], prices: PriceTable) -> None:
        self.tiers: dict[str, dict[str, str]] = cfg["tiers"]
        self.features: dict[str, str] = cfg.get("features", {})
        if "cheap" not in self.tiers:
            raise ValueError("llm_models.yaml에 'cheap' 등급이 필요합니다")
        for name, t in self.tiers.items():
            if not prices.has_model(t["model"]):
                raise ValueError(f"등급 {name}의 모델 {t['model']}이 단가표에 없습니다")

    def route(self, feature: str, level: BudgetLevel) -> Route:
        if level >= BudgetLevel.EXHAUSTED:
            raise BudgetExhausted("월 LLM 예산 소진 — LLM 기능이 중단되었습니다")
        tier = self.features.get(feature, "cheap")
        degraded = False
        if level >= BudgetLevel.DEGRADED and tier != "cheap":
            tier, degraded = "cheap", True
        t = self.tiers[tier]
        return Route(tier=tier, model=t["model"], effort=t.get("effort", "medium"), degraded=degraded)
