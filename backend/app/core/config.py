from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEKEN_", env_file=".env", extra="ignore")

    env: Literal["demo", "development", "test", "production"] = "demo"
    database_url: str = "sqlite+aiosqlite:///./meken.sqlite3"
    redis_url: str = ""
    auto_create_schema: bool = False
    provider_config: str = "data/providers.example.json"
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    ai_enabled: bool = False
    openai_api_key: SecretStr = SecretStr("")
    ai_model: str = "gpt-4.1-mini"
    ai_timeout_seconds: float = Field(default=12, ge=1, le=30)
    ai_daily_user_calls: int = Field(default=40, ge=1)
    ai_daily_global_calls: int = Field(default=2000, ge=1)
    source_timeout_seconds: float = Field(default=6, ge=0.05, le=30)
    source_concurrency: int = Field(default=6, ge=1, le=20)
    refresh_interval_seconds: int = Field(default=300, ge=10)
    fresh_seconds: int = Field(default=300, ge=1)
    max_catalog_age_seconds: int = Field(default=86400, ge=60)
    max_snapshot_items: int = Field(default=20000, ge=1)
    metrics_token: SecretStr = SecretStr("")
    privacy_url: str = ""
    terms_url: str = ""
    retention_days: int = Field(default=90, ge=1, le=365)

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.ai_enabled and not self.openai_api_key.get_secret_value():
            raise ValueError("AI enabled but MEKEN_OPENAI_API_KEY is empty")
        if self.env == "production":
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("Production requires PostgreSQL/asyncpg")
            if not self.redis_url or self.auto_create_schema:
                raise ValueError("Production requires Redis and explicit Alembic migrations")
            if "*" in self.allowed_hosts or len(self.metrics_token.get_secret_value()) < 32:
                raise ValueError(
                    "Set explicit hosts and a random metrics token of at least 32 characters"
                )
            if not self.privacy_url.startswith("https://") or not self.terms_url.startswith("https://"):
                raise ValueError("Production requires published HTTPS privacy and terms URLs")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
