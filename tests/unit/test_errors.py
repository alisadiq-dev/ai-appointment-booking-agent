from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError, register_exception_handlers


def _app_raising(error: Exception) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise error

    return app


def test_app_error_uses_the_standard_error_format() -> None:
    error = AppError(code="slot_taken", message="That slot is no longer free.", status_code=409)

    response = TestClient(_app_raising(error)).get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "slot_taken", "message": "That slot is no longer free."}
    }


def test_app_error_can_carry_response_headers() -> None:
    error = AppError(
        code="unauthorized",
        message="nope",
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )

    response = TestClient(_app_raising(error)).get("/boom")

    assert response.headers["www-authenticate"] == "Bearer"
