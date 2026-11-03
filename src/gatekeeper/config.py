"""Runtime configuration. Boot-time validation refuses unsafe production setups."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ADMIN_API_VERSION = "2026-10"
MYSHOPIFY_DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]*\.myshopify\.com")


class Settings(BaseSettings):
    # hide_input_in_errors: a failed boot must not print secrets from the environment into the logs.
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False, hide_input_in_errors=True)

    env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(default="postgresql://gatekeeper:gatekeeper@127.0.0.1:54329/gatekeeper", repr=False)
    demo_mode: bool = False
    demo_autorun: int = 0
    # A demo on the open internet: only the reviewer demo token, rate limits, no model key, and periodic resets.
    public_demo: bool = False
    demo_reset_minutes: int = Field(default=0, ge=0)
    rate_limit_per_minute: int = Field(default=20, ge=1)
    rate_limit_global_per_minute: int = Field(default=300, ge=1)
    # An open review page polls about 30 times a minute.
    read_limit_per_minute: int = Field(default=300, ge=1)
    read_limit_global_per_minute: int = Field(default=1200, ge=1)
    # Proxies in front of the app that append to X-Forwarded-For. 0 trusts only the socket peer.
    trusted_proxy_hops: int = Field(default=0, ge=0)
    max_request_bytes: int = Field(default=64 * 1024, ge=1024)
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    static_dir: Path | None = None

    shopify_adapter: Literal["fixture", "live"] = "fixture"
    shopify_shop_domain: str = "demo-store.myshopify.com"
    shopify_access_token: SecretStr | None = None
    # Dev Dashboard apps get 24-hour tokens from the client credentials grant. With these set, tokens renew themselves.
    shopify_client_id: str | None = None
    shopify_client_secret: SecretStr | None = None
    shopify_api_version: str = ADMIN_API_VERSION
    shopify_webhook_secret: SecretStr | None = None
    writeback_enabled: bool = False

    llm_provider: Literal["auto", "anthropic", "heuristic"] = "auto"
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_judge_model: str | None = None
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    allow_demo_model_in_prod: bool = False
    # Empty means any Host. Set for local stacks, so a page on another origin cannot reach them by DNS rebinding.
    allowed_hosts: str = ""

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
        problems: list[str] = []
        # These hold in every environment: public demo tokens must never sit in front of a real store.
        if self.shopify_adapter == "live" and self.demo_mode:
            problems.append("DEMO_MODE cannot be combined with the live Shopify adapter")
        if self.shopify_adapter == "live" and "shopify_shop_domain" not in self.model_fields_set:
            problems.append("SHOPIFY_SHOP_DOMAIN must be set explicitly with the live adapter")
        # "auto" without a key also resolves to the template model (context.build_provider).
        template_model = self.llm_provider == "heuristic" or (
            self.llm_provider == "auto" and not self.anthropic_api_key
        )
        if self.shopify_adapter == "live" and self.writeback_enabled and template_model:
            problems.append("the template model cannot write to a live store")
        if bool(self.shopify_client_id) != bool(self.shopify_client_secret):
            problems.append("SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET must be set together")
        if self.shopify_client_id and self.shopify_access_token:
            problems.append("set SHOPIFY_ACCESS_TOKEN or the client credentials, not both")
        if self.public_demo:
            if not self.demo_mode:
                problems.append("PUBLIC_DEMO needs DEMO_MODE=1")
            if self.shopify_adapter != "fixture":
                problems.append("PUBLIC_DEMO only runs on the fixture store")
            if self.anthropic_api_key or self.llm_provider == "anthropic":
                problems.append("PUBLIC_DEMO must not hold a model key, because any visitor could spend it")
            if self.shopify_access_token or self.shopify_client_id:
                problems.append("PUBLIC_DEMO must not hold store credentials")
            # A wrong count either lets visitors pick their own rate-limit bucket or puts everyone in one.
            if "trusted_proxy_hops" not in self.model_fields_set:
                problems.append("PUBLIC_DEMO needs TRUSTED_PROXY_HOPS set explicitly, 0 when nothing sits in front")
            if not self.shopify_webhook_secret or not self.shopify_webhook_secret.get_secret_value().strip():
                problems.append(
                    "PUBLIC_DEMO needs SHOPIFY_WEBHOOK_SECRET, which the simulated merchant edit signs with"
                )
            # The page tells visitors how often their shared sandbox resets, so the interval must be real.
            if self.demo_reset_minutes < 5:
                problems.append("PUBLIC_DEMO needs DEMO_RESET_MINUTES of at least 5")
        elif 0 < self.demo_reset_minutes < 5:
            problems.append("DEMO_RESET_MINUTES must be 0 (never) or at least 5")
        if self.shopify_adapter == "live" and not MYSHOPIFY_DOMAIN.fullmatch(self.shopify_shop_domain):
            # Tokens and the client secret are sent to this host, so a typo must not send them elsewhere.
            problems.append("SHOPIFY_SHOP_DOMAIN must be the store's myshopify.com domain")
        if self.env != "production":
            if problems:
                raise ValueError("; ".join(problems))
            return self
        if self.shopify_adapter != "live":
            problems.append("production requires SHOPIFY_ADAPTER=live; the fixture store is for demos and tests")
        if self.demo_mode:
            problems.append("DEMO_MODE must be off in production")
        if not self.reviewer_tokens or not self.reviewer_tokens.get_secret_value().strip():
            problems.append("REVIEWER_TOKENS is required in production")
        if not self.shopify_webhook_secret or not self.shopify_webhook_secret.get_secret_value().strip():
            problems.append("SHOPIFY_WEBHOOK_SECRET is required in production")
        if self.llm_provider == "auto":
            problems.append("LLM_PROVIDER must be explicit in production")
        if self.llm_provider == "heuristic" and not self.allow_demo_model_in_prod:
            problems.append("the heuristic demo model is refused in production")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.shopify_adapter == "live" and not (self.shopify_access_token or self.shopify_client_id):
            problems.append(
                "the live adapter needs SHOPIFY_ACCESS_TOKEN, or SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
