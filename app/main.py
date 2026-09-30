from fastapi import FastAPI

from app.api.routers import health


def create_app() -> FastAPI:
    app = FastAPI(title="AI Appointment Booking Agent")
    app.include_router(health.router)
    return app


app = create_app()
