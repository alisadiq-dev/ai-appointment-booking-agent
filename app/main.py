from fastapi import FastAPI

from app.api.routers import health
from app.core.errors import register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="AI Appointment Booking Agent")
    register_exception_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
