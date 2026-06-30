from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing for the selected source."""


class Settings(BaseSettings):
    app_name: str = "Camara Abierta"
    app_description: str = (
        "Plataforma de transparencia legislativa para seguimiento de proyectos de ley, "
        "legisladores, votaciones, Diario Oficial y reglamentos CGR."
    )
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"], alias="CORS_ORIGINS"
    )
    database_url: str = Field(..., alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    gobierno_actual_inicio: date = date(2026, 3, 11)
    admin_secret_key: str = Field(default="change-me", alias="ADMIN_SECRET_KEY")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="admin", alias="ADMIN_PASSWORD")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    openwebui_url: str = Field(default="http://localhost:3000", alias="OPENWEBUI_URL")
    openwebui_api_key: str = Field(default="", alias="OPENWEBUI_API_KEY")
    openwebui_model: str = Field(default="llama3", alias="OPENWEBUI_MODEL")
    file_process_timeout: int = Field(default=120, alias="FILE_PROCESS_TIMEOUT")
    # Bills AI summary (ADR-0019): Claude is the sole provider for the layered
    # bill summaries. Gemini/OpenWebUI above are retained for the norms pipeline.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-haiku-4-5", alias="ANTHROPIC_MODEL")
    ai_summary_prompt_version: str = Field(
        default="v2", alias="AI_SUMMARY_PROMPT_VERSION"
    )
    # Global gate for the bills AI summary feature (ADR-0019). Default off so
    # a fresh ``ingestors bills`` scan does not burn LLM budget; flip on per
    # environment when ready (and use ``ai bills regenerate --bulletin X``
    # for single-bill smoke tests). Applies to both the auto-enqueue in
    # ``sync_bill`` and the ``ai bills regenerate`` backfill.
    ai_summary_enabled: bool = Field(default=False, alias="AI_SUMMARY_ENABLED")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    notification_email: str | None = Field(default=None, alias="NOTIFICATION_EMAIL")
    notification_from_email: str = Field(
        default="noreply@camaraabierta.cl",
        alias="NOTIFICATION_FROM_EMAIL",
    )
    ingestor_base_url_camara: str = Field(
        default="https://opendata.congreso.cl/wscamaradiputados.asmx/",
        alias="INGESTOR_BASE_URL_CAMARA",
    )
    ingestor_base_url_opendata_camara: str = Field(
        default="https://opendata.camara.cl/camaradiputados/WServices/",
        alias="INGESTOR_BASE_URL_OPENDATA_CAMARA",
    )
    ingestor_base_url_senado: str = Field(
        default="https://tramitacion.senado.cl/wspublico/",
        alias="INGESTOR_BASE_URL_SENADO",
    )
    ingestor_base_url_senado_web: str = Field(
        default="https://web-back.senado.cl/",
        alias="INGESTOR_BASE_URL_SENADO_WEB",
    )
    ingestor_base_url_bcn: str = Field(
        default="https://datos.bcn.cl/",
        alias="INGESTOR_BASE_URL_BCN",
    )
    ingestor_bills_start_year: int = Field(
        default=1990,
        alias="INGESTOR_BILLS_START_YEAR",
    )
    ingestor_base_url_restsil: str = Field(
        default="https://restsil.senado.cl/v3/",
        alias="INGESTOR_BASE_URL_RESTSIL",
    )
    ingestor_restsil_api_key: str | None = Field(
        default=None,
        alias="INGESTOR_RESTSIL_API_KEY",
    )
    ingestor_bills_source: Literal["restsil", "opendata"] = Field(
        default="restsil",
        alias="INGESTOR_BILLS_SOURCE",
    )
    ingestor_senate_votes_source: Literal["restsil", "wspublico"] = Field(
        default="restsil",
        alias="INGESTOR_SENATE_VOTES_SOURCE",
    )
    ingestor_chamber_votes_source: Literal["bulk", "bill_detail"] = Field(
        default="bulk",
        alias="INGESTOR_CHAMBER_VOTES_SOURCE",
    )
    ingestor_opendata_async_concurrency: int = Field(
        default=10,
        alias="INGESTOR_OPENDATA_ASYNC_CONCURRENCY",
    )
    ingestor_chamber_votes_max_years_per_tick: int = Field(
        default=10,
        alias="INGESTOR_CHAMBER_VOTES_MAX_YEARS_PER_TICK",
    )
    ingestor_restsil_page_size: int = Field(
        default=100,
        alias="INGESTOR_RESTSIL_PAGE_SIZE",
    )
    ingestor_restsil_max_pages_per_tick: int = Field(
        default=100,
        alias="INGESTOR_RESTSIL_MAX_PAGES_PER_TICK",
    )
    ingestor_restsil_async_concurrency: int = Field(
        default=10,
        alias="INGESTOR_RESTSIL_ASYNC_CONCURRENCY",
    )

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
