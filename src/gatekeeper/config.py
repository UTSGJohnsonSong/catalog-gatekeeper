"""Runtime configuration. Boot-time validation refuses unsafe production setups."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_API_VERSION = "2026-10"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql://gatekeeper:gatekeeper@127.0.0.1:54329/gatekeeper"
    demo_mode: bool = False
    demo_autorun: int = 0
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    static_dir: Path | None = None

    shopify_adapter: Literal["fixture", "live"] = "fixture"
    shopify_shop_domain: str = "demo-store.myshopify.com"
    shopify_access_token: SecretStr | None = None
    shopify_api_version: str = ADMIN_API_VERSION
    shopify_webhook_secret: SecretStr | None = None
    writeback_enabled: bool = False

    llm_provider: Literal["auto", "anthropic", "heuristic"] = "auto"
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_judge_model: str | None = None
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    allow_demo_model_in_prod: bool = False

    max_model_calls: int = 8
    max_tool_calls: int = 16
    max_propose_calls: int = 2
    max_tokens_per_run: int = 80_000
    max_cost_per_run_usd: float = 0.25
    max_seconds_per_run: float = 120.0
    daily_cost_cap_usd: float = 5.0

    reviewer_tokens: SecretStr | None = Field(
        default=None, description="Comma-separated name:role:sha256hex entries. Only hashes are stored."
    )
    mcp_db_password: SecretStr | None = None
    app_db_password: SecretStr | None = None
    worker_poll_seconds: float = 1.0

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> Settings:
        if self.env != "production":
            return self
        problems: list[str] = []
        if self.demo_mode:
            problems.append("DEMO_MODE must be off in production")
        if not self.reviewer_tokens or not self.reviewer_tokens.get_secret_value().strip():
            problems.append("REVIEWER_TOKENS is required in production")
        if not self.shopify_webhook_secret:
            problems.append("SHOPIFY_WEBHOOK_SECRET is required in production")
        if self.llm_provider == "auto":
            problems.append("LLM_PROVIDER must be explicit in production")
        if self.llm_provider == "heuristic" and not self.allow_demo_model_in_prod:
            problems.append("the heuristic demo model is refused in production")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.shopify_adapter == "live" and not self.shopify_access_token:
            problems.append("SHOPIFY_ACCESS_TOKEN is required with the live adapter")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
