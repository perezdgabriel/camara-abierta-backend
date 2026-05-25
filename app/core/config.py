from datetime import date
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    elasticsearch_url: str = Field(
        default="http://localhost:9200", alias="ELASTICSEARCH_URL"
    )
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    openwebui_url: str = Field(default="http://localhost:3000", alias="OPENWEBUI_URL")
    openwebui_api_key: str = Field(default="", alias="OPENWEBUI_API_KEY")
    openwebui_model: str = Field(default="llama3", alias="OPENWEBUI_MODEL")
    file_process_timeout: int = Field(default=120, alias="FILE_PROCESS_TIMEOUT")
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
    ingestor_bills_start_year: int = Field(
        default=2026,
        alias="INGESTOR_BILLS_START_YEAR",
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
