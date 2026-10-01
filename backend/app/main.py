from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin_catalog import router as admin_catalog_router
from app.api.admin_planning import router as admin_planning_router
from app.api.admin_users import router as admin_users_router
from app.api.auth import router as auth_router
from app.api.cancellation import router as cancellation_router
from app.api.cards import router as cards_router
from app.api.frame import router as frame_router
from app.api.health import router as health_router
from app.api.manager import router as manager_router
from app.api.reports import router as reports_router
from app.api.results import router as results_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_response(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's default 422 body mirrors rejected input.  Keep validation
        # locations and messages but never reflect a browser-supplied internal id.
        detail = []
        for error in exc.errors():
            safe_error = {}
            for key, value in error.items():
                if key == "input":
                    continue
                if key == "ctx" and isinstance(value, dict):
                    safe_error[key] = {
                        k: str(v) if isinstance(v, Exception) else v
                        for k, v in value.items()
                    }
                else:
                    safe_error[key] = value
            if "case_id" in safe_error.get("loc", ()):
                safe_error = {
                    "type": safe_error.get("type", "value_error"),
                    "loc": ("body",),
                    "msg": "Invalid request context",
                }
            detail.append(safe_error)
        return JSONResponse(status_code=422, content={"detail": detail})

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(cancellation_router)
    app.include_router(results_router)
    app.include_router(cards_router)
    app.include_router(frame_router)
    app.include_router(manager_router)
    app.include_router(admin_users_router)
    app.include_router(admin_planning_router)
    app.include_router(admin_catalog_router)
    app.include_router(reports_router)
    return app


app = create_app()
