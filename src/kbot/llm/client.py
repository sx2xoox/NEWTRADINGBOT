"""Anthropic API의 유일한 진입점.

모든 LLM 호출은 여기서
  1) 프롬프트 비밀값 검사 → 2) 캐시 조회 → 3) 예산 상태에 따른 모델 라우팅
  → 4) 최악 비용 예약 → 5) 호출 → 6) usage로 정산·원장 기록 → 7) 캐시 저장
순서를 거친다. 다른 모듈은 anthropic SDK를 import할 수 없다(import 계약으로 강제).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from kbot.core import timeutil
from kbot.llm.budget import BudgetManager
from kbot.llm.errors import LLMCallFailed, PromptSecretLeak
from kbot.llm.pricing import TokenUsage, estimate_tokens
from kbot.llm.router import ModelRouter
from kbot.storage import db

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    cost_usd: float
    cached: bool
    degraded: bool = False          # 예산 90%↑로 저가 모델로 강등되었는지
    truncated: bool = False         # max_tokens로 잘렸는지
    usage: TokenUsage | None = None


class LLMClient:
    def __init__(
        self,
        engine: Engine,
        budget: BudgetManager,
        router: ModelRouter,
        *,
        api_key: str | None,
        secrets: Iterable[str] = (),
        sdk_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.engine = engine
        self.budget = budget
        self.router = router
        self._secrets = [s for s in secrets if s and len(s) >= 6]
        if api_key:
            self._secrets.append(api_key)
        self._api_key = api_key
        self._sdk_factory = sdk_factory
        self._sdk: Any = None

    # ── 내부 ────────────────────────────────────────────────
    def _client(self) -> Any:
        if self._sdk is None:
            if self._sdk_factory is not None:
                self._sdk = self._sdk_factory()
            else:
                if not self._api_key:
                    raise LLMCallFailed("ANTHROPIC_API_KEY가 설정되지 않았습니다 (.env)")
                self._sdk = anthropic.Anthropic(api_key=self._api_key, max_retries=2, timeout=300.0)
        return self._sdk

    def _check_secrets(self, *texts: str) -> None:
        for t in texts:
            for s in self._secrets:
                if s in t:
                    raise PromptSecretLeak("프롬프트에 비밀값이 포함되어 LLM 전송을 차단했습니다")

    @staticmethod
    def _cache_id(feature: str, prompt_version: str, cache_key: str) -> str:
        return hashlib.sha256(f"{feature}\x1f{prompt_version}\x1f{cache_key}".encode()).hexdigest()

    def _cache_get(self, cid: str) -> tuple[str, str] | None:
        t = db.llm_cache
        with self.engine.connect() as conn:
            r = conn.execute(select(t.c.response_text, t.c.model).where(t.c.cache_key == cid)).first()
        return (r.response_text, r.model) if r else None

    def _cache_put(self, cid: str, feature: str, model: str, prompt_version: str, text: str, cost: float) -> None:
        stmt = sqlite_insert(db.llm_cache).values(
            cache_key=cid, feature=feature, model=model, prompt_version=prompt_version,
            response_text=text, cost_usd=cost, created_at=timeutil.now())
        with self.engine.begin() as conn:
            conn.execute(stmt.on_conflict_do_nothing(index_elements=["cache_key"]))

    # ── 공개 API ────────────────────────────────────────────
    def complete(
        self,
        *,
        feature: str,
        system: str,
        user: str,
        max_tokens: int = 2048,
        cache_key: str | None = None,
        prompt_version: str = "v1",
        urgent: bool = False,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        """단발 호출. 예산 초과 시 BudgetExhausted / DailyLimitExceeded를 그대로 올린다."""
        self._check_secrets(system, user)

        cid = self._cache_id(feature, prompt_version, cache_key) if cache_key else None
        if cid:
            hit = self._cache_get(cid)
            if hit:
                return LLMResult(text=hit[0], model=hit[1], cost_usd=0.0, cached=True)

        route = self.router.route(feature, self.budget.level())
        est_prompt = estimate_tokens(system) + estimate_tokens(user)
        reserve_amt = self.budget.prices.max_cost(route.model, est_prompt, max_tokens)
        res = self.budget.reserve(feature, reserve_amt, urgent=urgent)

        output_config: dict[str, Any] = {"effort": route.effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        try:
            resp = self._client().messages.create(
                model=route.model,
                max_tokens=max_tokens,
                # 시스템 프롬프트는 고정 → 프롬프트 캐싱 (동일 기능 반복 호출 시 입력 비용 절감)
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except anthropic.APIStatusError as e:
            self.budget.release(res)
            raise LLMCallFailed(f"LLM API 오류 (HTTP {e.status_code})") from e
        except anthropic.APIConnectionError as e:
            # 연결 오류: 서버가 처리했는지 알 수 없지만 응답(usage)이 없으므로 예약만 해제한다.
            self.budget.release(res)
            raise LLMCallFailed("LLM API 연결 실패") from e

        usage = TokenUsage.from_api_usage(resp.usage)
        model_used = getattr(resp, "model", None) or route.model
        if not self.budget.prices.has_model(model_used):
            model_used = route.model
        cost = self.budget.settle(res, model=model_used, usage=usage,
                                  request_id=getattr(resp, "_request_id", None) or getattr(resp, "id", None),
                                  note=feature)

        if resp.stop_reason == "refusal":
            raise LLMCallFailed("모델이 요청을 거절했습니다 (stop_reason=refusal)")
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        truncated = resp.stop_reason == "max_tokens"
        if cid and not truncated:
            self._cache_put(cid, feature, model_used, prompt_version, text, cost)
        return LLMResult(text=text, model=model_used, cost_usd=cost, cached=False,
                         degraded=route.degraded, truncated=truncated, usage=usage)

    def complete_json(self, **kwargs: Any) -> tuple[Any, LLMResult]:
        """구조화 출력(JSON 스키마) 호출. 파싱된 객체와 원 결과를 반환."""
        if "json_schema" not in kwargs:
            raise ValueError("json_schema가 필요합니다")
        result = self.complete(**kwargs)
        try:
            return json.loads(result.text), result
        except json.JSONDecodeError as e:
            raise LLMCallFailed("LLM 응답이 JSON 형식이 아닙니다") from e
