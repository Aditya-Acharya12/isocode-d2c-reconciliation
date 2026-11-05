import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """An expected failure, returned to clients as {code, message, details}."""

    def __init__(self, status_code: int, code: str, message: str, details: object = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _body(code: str, message: str, details: object = None) -> dict:
    return {"code": code, "message": message, "details": details}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            _body("validation_error", "Some fields are invalid.", jsonable_encoder(details)), 422
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(_body(f"http_{exc.status_code}", str(exc.detail)), exc.status_code)

    @app.exception_handler(OperationalError)
    async def handle_database_down(_: Request, exc: OperationalError) -> JSONResponse:
        # Most often Neon waking from scale-to-zero. The frontend retries safe requests.
        logger.warning("Database unavailable: %s", exc.__class__.__name__)
        return JSONResponse(
            _body("database_unavailable", "The database is starting. Please try again shortly."),
            503,
            headers={"Retry-After": "3"},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(_body("internal_error", "Something went wrong on the server."), 500)
