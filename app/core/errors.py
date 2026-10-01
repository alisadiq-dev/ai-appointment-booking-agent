import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Fixed codes and messages for framework-raised HTTP errors (unknown route, wrong method).
# The framework's own detail text is never used, so nothing request-specific is echoed back.
_HTTP_ERRORS = {
    404: ("not_found", "The requested resource was not found."),
    405: ("method_not_allowed", "This method is not allowed for this resource."),
}


class AppError(Exception):
    """An expected error that maps to an HTTP response in the standard error format."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.headers = headers


class DatabaseUnavailableError(AppError):
    """The database cannot be reached right now. Fails closed."""

    def __init__(self) -> None:
        super().__init__(
            code="database_unavailable",
            message="The database is temporarily unavailable.",
            status_code=503,
        )


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)  # only registered for AppError
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
    )


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    # Field names and error types only: never echo the submitted values back.
    problems = [
        f"{'.'.join(str(part) for part in err['loc'][1:]) or err['loc'][0]} ({err['type']})"
        for err in exc.errors()[:5]
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": f"Invalid request: {', '.join(problems)}",
            }
        },
    )


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)  # only registered for StarletteHTTPException
    code, message = _HTTP_ERRORS.get(exc.status_code, ("http_error", "The request failed."))
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
        headers=exc.headers,  # keeps `Allow` on a 405
    )


async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # The stack trace goes to the log for operators; the caller learns nothing about the cause.
    logger.error("unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An internal error occurred."}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
