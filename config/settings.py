"""
Application settings loaded from environment variables via pydantic-settings.
All credentials and configuration are sourced from .env (never hardcoded).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Neo4jSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEO4J_", env_file=".env", extra="ignore")

    uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    user: str = Field(default="neo4j", alias="NEO4J_USER")
    password: str = Field(alias="NEO4J_PASSWORD")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


class SnowflakeSettings(BaseSettings):
    account: str = Field(alias="SNOWFLAKE_ACCOUNT")
    user: str = Field(alias="SNOWFLAKE_USER")
    password: str = Field(alias="SNOWFLAKE_PASSWORD")
    warehouse: str = Field(default="COMPUTE_WH", alias="SNOWFLAKE_WAREHOUSE")
    database: str = Field(default="CLINICAL_TRIALS", alias="SNOWFLAKE_DATABASE")
    schema_name: str = Field(default="PUBLIC", alias="SNOWFLAKE_SCHEMA")
    role: str = Field(default="SYSADMIN", alias="SNOWFLAKE_ROLE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    def get_snowflake_connection(self) -> Any:
        """Return a live snowflake.connector connection using these settings."""
        import snowflake.connector  # type: ignore[import-untyped]

        return snowflake.connector.connect(
            account=self.account,
            user=self.user,
            password=self.password,
            warehouse=self.warehouse,
            database=self.database,
            schema=self.schema_name,
            role=self.role,
        )


class LLMSettings(BaseSettings):
    provider: str = Field(default="openai", alias="LLM_PROVIDER")
    model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


class PipelineSettings(BaseSettings):
    clinicaltrials_base_url: str = Field(
        default="https://clinicaltrials.gov/api/v2",
        alias="CLINICALTRIALS_BASE_URL",
    )
    rxnorm_base_url: str = Field(
        default="https://rxnav.nlm.nih.gov/REST",
        alias="RXNORM_BASE_URL",
    )
    default_therapeutic_area: str = Field(
        default="oncology",
        alias="DEFAULT_THERAPEUTIC_AREA",
    )
    batch_size: int = Field(default=500, alias="BATCH_SIZE")
    llm_concurrency: int = Field(default=10, alias="LLM_CONCURRENCY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def therapeutic_area(self) -> str:
        return self.default_therapeutic_area

    @field_validator("batch_size", mode="before")
    @classmethod
    def validate_batch_size(cls, v: Any) -> int:
        v = int(v)
        if v <= 0:
            raise ValueError("batch_size must be positive")
        return v


class APISettings(BaseSettings):
    host: str = Field(default="0.0.0.0", alias="API_HOST")
    port: int = Field(default=8000, alias="API_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


class Settings(BaseSettings):
    """Root settings object — compose all sub-settings here."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    snowflake: SnowflakeSettings | None = None
    llm: LLMSettings = Field(default_factory=LLMSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    api: APISettings = Field(default_factory=APISettings)

    def model_post_init(self, __context: Any) -> None:
        # Snowflake settings are optional — only load if account is set
        try:
            self.snowflake = SnowflakeSettings()  # type: ignore[assignment]
        except Exception:
            self.snowflake = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()

settings = Settings()
