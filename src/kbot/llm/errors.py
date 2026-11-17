"""LLM 계층 예외. 호출자는 이 예외들을 잡아 'LLM 없이' 동작을 이어가야 한다."""


class LLMError(Exception):
    """LLM 계층 예외의 최상위."""


class BudgetExhausted(LLMError):
    """월 예산 소진(100%) 또는 이번 호출이 월 잔액을 넘음 — LLM 기능 중단."""


class DailyLimitExceeded(LLMError):
    """오늘 일일 한도 초과 — 급하지 않은 작업은 내일로 미룬다."""


class PromptSecretLeak(LLMError):
    """프롬프트에 비밀값(API 키 등)이 포함되어 전송을 차단함."""


class LLMCallFailed(LLMError):
    """API 호출 실패 (네트워크, 4xx/5xx, 거절 등)."""


class UnknownModelPricing(LLMError):
    """단가표(config/llm_pricing.yaml)에 없는 모델 — 비용을 계산할 수 없어 호출 차단."""
