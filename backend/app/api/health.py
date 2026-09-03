import psycopg
import redis
from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@router.get("/health/ready")
def ready() -> dict[str, str]:
    checks: dict[str, str] = {}

    try:
        with (
            psycopg.connect(
                settings.psycopg_database_url, connect_timeout=3
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = "failed"
        raise HTTPException(
            status_code=503,
            detail={"status": "failed", "checks": checks},
        ) from exc

    try:
        redis_client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=3
        )
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = "failed"
        raise HTTPException(
            status_code=503,
            detail={"status": "failed", "checks": checks},
        ) from exc

    return {"status": "ok", "postgres": checks["postgres"], "redis": checks["redis"]}
