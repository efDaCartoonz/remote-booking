from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    app_name: str = "Remote Desktop Manager"
    app_secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://nimda:change-me@postgres:5432/rdm"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
    backend_cors_origins: str = "http://localhost:8080,http://127.0.0.1:8080"
    omnidesk_base_url: str = "https://iridi.omnidesk.ru"
    omnidesk_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @cached_property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.backend_cors_origins.split(",")
            if origin.strip()
        ]

    @cached_property
    def psycopg_database_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


settings = Settings()
