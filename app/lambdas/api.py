"""API handler: adapts the FastAPI app to Lambda via Mangum.

The Function URL is AuthType NONE; the shared-secret header check lives in the
app (validate against the SSM-provided secret in middleware).
"""

from typing import Any

from mangum import Mangum

from app.main import app

_mangum_handler = Mangum(app, lifespan="off")


def _fix_trailing_slash(event: dict[str, Any]) -> dict[str, Any]:
    """Work around a Mangum/API-Gateway-v2 path mismatch.

    For Function URL / HTTP API v2 events, AWS strips the trailing slash from
    ``requestContext.http.path`` (the field Mangum reads) but preserves it in
    ``rawPath``. Mangum's ASGI scope ends up missing the slash the client
    actually sent, so Starlette's own trailing-slash redirect "corrects" it
    right back to the original URL — an infinite 307 redirect loop for any
    route registered with a trailing slash (e.g. sqladmin's mounted `/admin/`
    index). Re-sync the two fields before handing the event to Mangum.
    """
    raw_path = event.get("rawPath")
    http = event.get("requestContext", {}).get("http", {})
    if raw_path and http.get("path") != raw_path:
        http["path"] = raw_path
    return event


def handler(event: dict[str, Any], context: Any) -> Any:
    return _mangum_handler(_fix_trailing_slash(event), context)
