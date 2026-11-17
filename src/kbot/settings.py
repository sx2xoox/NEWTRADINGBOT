"""환경 변수(.env)와 설정 파일(config/*.yaml) 로더.

비밀값은 모두 SecretStr로 감싸 repr/로그에 노출되지 않는다.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    trading_mode: TradingMode = TradingMode.PAPER
    kbot_data_dir: Path = PROJECT_ROOT / "var"
    config_dir: Path = CONFIG_DIR

    anthropic_api_key: SecretStr | None = None
    llm_monthly_budget_usd: float = Field(default=150.0, gt=0)
    llm_budget_reset_day: int = Field(default=1, ge=1, le=31)

    kis_paper_app_key: SecretStr | None = None
    kis_paper_app_secret: SecretStr | None = None
    kis_paper_account_no: SecretStr | None = None
    kis_paper_account_product: str = "01"

    dart_api_key: SecretStr | None = None
    sec_user_agent: str | None = None
    ecos_api_key: SecretStr | None = None
    krx_id: SecretStr | None = None
    krx_pw: SecretStr | None = None
    discord_webhook_url: SecretStr | None = None

    @field_validator(
        "anthropic_api_key", "kis_paper_app_key", "kis_paper_app_secret", "kis_paper_account_no",
        "dart_api_key", "ecos_api_key", "krx_id", "krx_pw", "discord_webhook_url", mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return None if v in ("", None) else v

    @field_validator("sec_user_agent", mode="before")
    @classmethod
    def _empty_ua(cls, v: Any) -> Any:
        return None if v in ("", None) else v

    @property
    def db_path(self) -> Path:
        return self.kbot_data_dir / "kbot.db"

    def secret_values(self) -> list[str]:
        """로그 마스킹용: 설정된 모든 비밀값의 원문 목록."""
        out: list[str] = []
        for _name, value in self:
            if isinstance(value, SecretStr):
                raw = value.get_secret_value()
                if raw:
                    out.append(raw)
        return out

    def load_yaml(self, name: str) -> dict[str, Any]:
        path = self.config_dir / name
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
