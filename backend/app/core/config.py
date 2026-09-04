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
    auth_session_cookie_name: str = "rdm_session"
    auth_session_ttl_minutes: int = 480
    auth_cookie_secure: bool = True
    auth_cookie_samesite: str = "lax"
    frame_session_ttl_minutes: int = 15
    omnidesk_base_url: str = "https://iridi.omnidesk.ru"
    omnidesk_staff_email: str = ""
    omnidesk_api_key: str = ""
    omnidesk_timeout_seconds: float = 5.0
    notification_max_attempts: int = 3
    notification_retry_seconds: int = 60
    notification_lock_seconds: int = 300
    notification_http_timeout_seconds: float = 5.0
    reminder_l2_interval_seconds: int = 600
    reminder_l2_escalation_after_count: int = 2
    reminder_l1_interval_seconds: int = 600
    reminder_l1_escalation_after_count: int = 2
    reminder_l1_manager_repeat_seconds: int = 1800
    reminder_l1_informed_interval_seconds: int = 1800
    reminder_scan_interval_seconds: int = 60
    reminder_batch_size: int = 100
    telegram_bot_token: str = ""
    telegram_api_url: str = "https://api.telegram.org"
    bitrix24_bot_webhook_url: str = ""
    notification_card_base_url: str = ""

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

    @property
    def reminder_batch_limit(self) -> int:
        return min(max(self.reminder_batch_size, 1), 500)


settings = Settings()
