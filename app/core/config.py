from datetime import date
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Diario Oficial de Chile — API"
    app_description: str = (
        "Backend principal para seguimiento legislativo, Diario Oficial y reglamentos CGR."
    )
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    database_url: str = Field(..., alias="DATABASE_URL")
    gobierno_actual_inicio: date = date(2026, 3, 11)
    admin_secret_key: str = Field(default="change-me", alias="ADMIN_SECRET_KEY")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="admin", alias="ADMIN_PASSWORD")
    elasticsearch_url: str = Field(default="http://localhost:9200", alias="ELASTICSEARCH_URL")

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
