from fastapi import APIRouter, FastAPI, Request, Response

from app.api.routes import audit, auth, diagnostics, partners, workspace
from app.core.config import get_settings
from app.core.errors import register_error_handlers

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def create_app() -> FastAPI:
    settings = get_settings()
    # No CORS middleware: browsers reach the API through a same-origin rewrite.
    app = FastAPI(
        title="D2C Reconciliation API",
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/api/healthz", tags=["health"])
    def healthz() -> dict:
        # Never touches the database: checking on the server must not wake Neon
        # or use up its monthly compute allowance.
        return {"status": "ok", "demo_mode": settings.demo_mode}

    v1 = APIRouter(prefix="/api/v1")
    for module in (auth, workspace, partners, audit, diagnostics):
        v1.include_router(module.router)
    app.include_router(v1)
    return app


app = create_app()
