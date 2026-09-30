from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
