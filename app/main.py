from fastapi import FastAPI

from app.api.router import router as api_router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=settings.app_description,
        version=settings.app_version,
    )
    app.include_router(api_router)
    return app


app = create_app()
