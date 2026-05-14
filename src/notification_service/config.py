from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="local")
    database_url: str = Field(
        default="postgresql+psycopg://notification:notification@localhost:5432/notification_service"
    )
    http_timeout_seconds: float = Field(default=5.0)
    worker_batch_size: int = Field(default=10)
    worker_poll_interval_seconds: float = Field(default=5.0)
    max_attempts: int = Field(default=5)
    target_host_allowlist: str = Field(default="")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
