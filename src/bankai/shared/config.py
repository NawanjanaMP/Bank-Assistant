"""Central, typed configuration.

Every value comes from the environment (12-factor). Secrets are expected to be
injected by the platform (Kubernetes Secrets / Vault agent), never baked into images.
Unsafe combinations are rejected at startup rather than discovered in production.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"


class AuthMode(StrEnum):
    OIDC = "oidc"  # validate bank IdP access tokens against JWKS
    DEV = "dev"  # static dev identity - forbidden outside local/test


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BANKAI_", env_file=".env", extra="ignore")

    env: Environment = Environment.LOCAL
    service_name: str = "bankai-orchestrator"
    log_level: str = "INFO"

    # ---- Identity (Bank's identity provider) ----
    auth_mode: AuthMode = AuthMode.OIDC
    oidc_issuer: str = "http://localhost:8080/realms/bank"
    oidc_jwks_url: str | None = None  # derived from issuer when unset
    oidc_audience: str = "bank-assistant-api"
    oidc_allowed_azp: list[str] = Field(default_factory=lambda: ["bank-assistant-web"])
    customer_id_claim: str = "customer_id"
    step_up_max_age_seconds: int = 300  # write actions need a recent authentication
    step_up_acr_values: list[str] = Field(default_factory=lambda: ["aal2", "mfa"])

    # ---- Service-to-service user context (orchestrator -> MCP servers) ----
    context_token_signing_key: SecretStr = SecretStr("change-me-in-every-environment-0123456789abcdef")
    context_token_ttl_seconds: int = 60
    context_token_issuer: str = "bankai-orchestrator"  # noqa: S105 - an issuer name, not a secret

    # ---- Data stores ----
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://bankai:bankai@localhost:5432/bankai"
    session_ttl_seconds: int = 60 * 30
    max_history_messages: int = 40
    pii_vault_key: SecretStr = SecretStr("")  # urlsafe base64 Fernet key; required outside local/test

    # ---- LLMs ----
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_base_url: str | None = None
    self_hosted_base_url: str = "http://localhost:8000/v1"  # vLLM / TGI / Ollama OpenAI-compatible
    self_hosted_api_key: SecretStr = SecretStr("not-needed")
    self_hosted_model: str = "qwen2.5-32b-instruct"
    coordinator_model: str = "claude-haiku-4-5-20251001"
    specialist_model: str = "claude-sonnet-5"
    llm_primary: str = "anthropic"  # anthropic | self_hosted
    llm_fallback: str | None = "self_hosted"
    # Agents whose traffic must never leave the bank network (data residency / KYC data).
    self_hosted_only_agents: list[str] = Field(default_factory=list)
    llm_timeout_seconds: float = 45.0
    llm_max_output_tokens: int = 1024
    agent_max_steps: int = 6
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_seconds: int = 30

    # ---- MCP servers ----
    mcp_accounts_url: str = "http://localhost:9101/mcp"
    mcp_transactions_url: str = "http://localhost:9102/mcp"
    mcp_service_url: str = "http://localhost:9103/mcp"
    mcp_timeout_seconds: float = 15.0

    # ---- Core banking (used by MCP servers) ----
    core_banking_url: str = "http://localhost:9200"
    core_banking_api_key: SecretStr = SecretStr("dev-core-banking-key")

    # ---- Limits & cost ----
    user_rate_limit_per_minute: int = 20
    session_cost_limit_usd: float = 0.50
    user_daily_cost_limit_usd: float = 5.00
    max_input_chars: int = 2000

    # ---- Observability ----
    otel_exporter_endpoint: str | None = None  # e.g. http://otel-collector:4318
    metrics_enabled: bool = True

    # ---- Web ----
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @property
    def jwks_url(self) -> str:
        return self.oidc_jwks_url or f"{self.oidc_issuer.rstrip('/')}/protocol/openid-connect/certs"

    @property
    def is_production_like(self) -> bool:
        return self.env in (Environment.STAGING, Environment.PROD)

    @model_validator(mode="after")
    def _refuse_unsafe_configuration(self) -> Settings:
        if self.is_production_like:
            problems = []
            if self.auth_mode is AuthMode.DEV:
                problems.append("BANKAI_AUTH_MODE=dev is not allowed in staging/prod")
            if "change-me" in self.context_token_signing_key.get_secret_value():
                problems.append("BANKAI_CONTEXT_TOKEN_SIGNING_KEY must be set")
            if len(self.context_token_signing_key.get_secret_value()) < 32:
                problems.append("BANKAI_CONTEXT_TOKEN_SIGNING_KEY must be >= 32 chars")
            if not self.pii_vault_key.get_secret_value():
                problems.append("BANKAI_PII_VAULT_KEY must be set")
            if self.core_banking_api_key.get_secret_value().startswith("dev-"):
                problems.append("BANKAI_CORE_BANKING_API_KEY must be a real credential")
            if problems:
                raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
