from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router as api_router
from app.core.config import settings

# Paths that never require the shared-secret header: the health probe (used by
# the Function URL check) and the admin panel (which enforces its own login).
_EXEMPT_PREFIXES = ("/admin",)
_EXEMPT_PATHS = frozenset({"/health"})


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES)


def create_app(*, include_admin: bool = True) -> FastAPI:
    # Docs off in prod (CDK sets DOCS_ENABLED=false); on by default locally.
    docs_on = settings.docs_enabled
    app = FastAPI(
        title=settings.app_name,
        description=settings.app_description,
        version=settings.app_version,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _require_shared_secret(request: Request, call_next):
        # No-op when the secret is unset (local dev stays open); when set, every
        # non-exempt path must present the matching X-Camara-Api-Key header.
        if settings.api_shared_secret and not _is_exempt(request.url.path):
            if request.headers.get("X-Camara-Api-Key") != settings.api_shared_secret:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)
    if include_admin:
        from app.admin import setup_admin

        setup_admin(app)
    return app


app = create_app()
