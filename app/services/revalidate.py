"""Fire-and-forget cache revalidation ping to the frontend.

After a deployed ingest touches content, we POST the affected cache tags to the
frontend's on-demand revalidation route so Vercel expires the relevant ISR
entries immediately instead of waiting out their TTL (see the frontend contract
in docs/deploy/backend-agent-plan.md).

This is best-effort: a missing config or a non-2xx response is logged and
swallowed — it must **never** fail the ingest job (worst case, content just
waits for its normal ISR TTL).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5.0


def revalidate(tags: list[str]) -> None:
    """POST ``tags`` to the frontend revalidation route; never raises."""
    if not tags:
        return
    if not settings.frontend_url or not settings.frontend_revalidate_token:
        logger.debug("revalidate skipped: frontend URL/token not configured")
        return

    url = f"{settings.frontend_url.rstrip('/')}/api/revalidate"
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {settings.frontend_revalidate_token}"},
            json={"tags": tags},
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            logger.warning(
                "revalidate ping non-2xx: %s %s", response.status_code, response.text
            )
    except httpx.HTTPError as exc:  # network error, timeout, etc.
        logger.warning("revalidate ping failed: %s", exc)
