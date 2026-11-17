"""토큰 사용량 → 비용(USD) 계산. 단가는 config/llm_pricing.yaml에서만 읽는다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kbot.llm.errors import UnknownModelPricing

MTOK = 1_000_000


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0            # 캐시되지 않은 입력
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0           # (thinking 토큰 포함 — 출력으로 과금)

    @property
    def prompt_tokens(self) -> int:
        """단가 구간 판정용 전체 프롬프트 길이 (캐시 읽기·쓰기 포함)."""
        return self.input_tokens + self.cache_write_5m_tokens + self.cache_write_1h_tokens + self.cache_read_tokens

    @classmethod
    def from_api_usage(cls, usage: Any) -> TokenUsage:
        """Anthropic 응답의 usage 객체에서 변환. 캐시 쓰기 TTL 세부 내역이 있으면 사용."""
        def g(obj: Any, name: str) -> int:
            v = getattr(obj, name, None) if not isinstance(obj, dict) else obj.get(name)
            return int(v or 0)

        cw_total = g(usage, "cache_creation_input_tokens")
        detail = getattr(usage, "cache_creation", None) if not isinstance(usage, dict) else usage.get("cache_creation")
        if detail:
            cw5, cw1 = g(detail, "ephemeral_5m_input_tokens"), g(detail, "ephemeral_1h_input_tokens")
            if cw5 + cw1 != cw_total:   # 세부 합이 안 맞으면 보수적으로 1h 단가 적용
                cw5, cw1 = 0, cw_total
        else:
            cw5, cw1 = cw_total, 0
        return cls(
            input_tokens=g(usage, "input_tokens"),
            cache_write_5m_tokens=cw5,
            cache_write_1h_tokens=cw1,
            cache_read_tokens=g(usage, "cache_read_input_tokens"),
            output_tokens=g(usage, "output_tokens"),
        )


@dataclass(frozen=True, slots=True)
class PriceTier:
    max_prompt_tokens: int | None
    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float


@dataclass(frozen=True)
class PriceTable:
    models: dict[str, list[PriceTier]]
    batch_discount: float = 0.5
    source: str = ""
    verified_on: str = ""

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> PriceTable:
        models: dict[str, list[PriceTier]] = {}
        for name, spec in (cfg.get("models") or {}).items():
            tiers = [PriceTier(**t) for t in spec["tiers"]]
            # 상한 오름차순, None(무제한)은 마지막
            tiers.sort(key=lambda t: float("inf") if t.max_prompt_tokens is None else t.max_prompt_tokens)
            if tiers[-1].max_prompt_tokens is not None:
                raise ValueError(f"{name}: 마지막 단가 구간은 max_prompt_tokens: null 이어야 합니다")
            models[name] = tiers
        return cls(models=models, batch_discount=float(cfg.get("batch_discount", 0.5)),
                   source=cfg.get("source", ""), verified_on=str(cfg.get("verified_on", "")))

    def has_model(self, model: str) -> bool:
        return model in self.models

    def tier_for(self, model: str, prompt_tokens: int) -> PriceTier:
        tiers = self.models.get(model)
        if not tiers:
            raise UnknownModelPricing(f"단가표에 없는 모델: {model}")
        for t in tiers:
            if t.max_prompt_tokens is None or prompt_tokens <= t.max_prompt_tokens:
                return t
        return tiers[-1]

    def cost(self, model: str, usage: TokenUsage, *, batch: bool = False) -> float:
        t = self.tier_for(model, usage.prompt_tokens)
        usd = (
            usage.input_tokens * t.input
            + usage.cache_write_5m_tokens * t.cache_write_5m
            + usage.cache_write_1h_tokens * t.cache_write_1h
            + usage.cache_read_tokens * t.cache_read
            + usage.output_tokens * t.output
        ) / MTOK
        if batch:
            usd *= self.batch_discount
        return usd

    def max_cost(self, model: str, est_prompt_tokens: int, max_output_tokens: int, *,
                 batch: bool = False) -> float:
        """예약용 최악 비용: 입력 전체를 캐시 쓰기(1h, 가장 비싼 입력 단가)로, 출력은 max_tokens 전부로 가정."""
        t = self.tier_for(model, est_prompt_tokens)
        worst_in = max(t.input, t.cache_write_5m, t.cache_write_1h)
        usd = (est_prompt_tokens * worst_in + max_output_tokens * t.output) / MTOK
        return usd * (self.batch_discount if batch else 1.0)


def estimate_tokens(text: str) -> int:
    """보수적 토큰 추정 (네트워크 호출 없이). 한글 등 비 ASCII는 1자≈1토큰, ASCII는 3자≈1토큰.

    실제 비용은 응답 usage로 정산하므로, 여기서는 '과소추정하지 않는 것'이 목표다.
    """
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_n = len(text) - non_ascii
    return non_ascii + (ascii_n + 2) // 3 + 16
