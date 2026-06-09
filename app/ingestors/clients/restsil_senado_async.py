"""Async paged-fetch helpers for ``restsil.senado.cl/v3/``.

The sync :class:`RestsilSenadoClient` walks pages one-at-a-time. Each call to
the upstream takes ~4 seconds, so a cold-start backfill of ~87 pages of votes
(or ~73 pages of active bills) takes minutes when walked sequentially. The
HTTP work is overwhelmingly idle wait — exactly the shape that the existing
``senado_async.py`` helpers exploit for per-bulletin fan-out. We do the same
here for restsil's paged endpoints.

Pattern (mirrors ``senado_async.py``):

- Module-level ``async def`` entry points: ``afetch_votes_pages``,
  ``afetch_bills_pages``.
- Concurrency cap via ``asyncio.Semaphore`` (configurable via
  ``settings.ingestor_restsil_async_concurrency``, default 10).
- A single shared ``httpx.AsyncClient`` per call, opened in a context
  manager.
- Per-request retry via :mod:`tenacity` on transport / timeout errors.
  Failures return ``(offset, None)`` so the caller can log + count and keep
  going — we don't want one slow page to block ingest of everything else.

The sync :meth:`RestsilSenadoClient.iter_votes_desc` /
:meth:`iter_bills_desc` decide whether to invoke these helpers based on
``total`` from the first (sequentially fetched) page. Single-page or
watermark-cutoff-on-page-1 cases never reach this module — they stay in
the sync path, which is the right tradeoff for the common steady-state tick.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.ingestors.clients.base import (
    CongresoAPIError,
    CongresoParseError,
    _httpx,
    _log_retry,
)

logger = logging.getLogger(__name__)


def _require_api_key() -> str:
    key = settings.ingestor_restsil_api_key
    if not key:
        # Mirrors the sync client's guard so the failure mode is identical
        # regardless of which entry point hit first.
        from app.core.config import ConfigurationError

        raise ConfigurationError(
            "INGESTOR_RESTSIL_API_KEY is required when "
            "ingestor_bills_source or ingestor_senate_votes_source is "
            "'restsil'. Set the env var or flip the source flag."
        )
    return key


def _async_client():  # type: ignore[no-untyped-def]
    """Build a fresh ``httpx.AsyncClient`` configured for restsil.

    Headers match the sync client so both paths look identical to the
    upstream. Timeouts are generous because we observed 4s P50 latencies.
    """
    httpx = _httpx()
    timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "CamaraAbierta/1.0 (+https://camaraabierta.cl)",
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Apikey {_require_api_key()}",
        },
    )


# Tenacity decorator shared by both per-page coroutines. The retry policy
# matches the sync client (``base.py``): retry on transport / timeout
# errors, 3 attempts, exponential backoff capped at 30s. HTTP errors with
# a status code (e.g. 4xx/5xx) are NOT retried — those raise
# ``CongresoAPIError`` which propagates as a one-shot failure.
_RETRY = retry(
    retry=retry_if_exception_type((_httpx().TransportError, _httpx().TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    before_sleep=_log_retry,
    reraise=True,
)


@_RETRY
async def _afetch_page(
    client: Any, path: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Single GET against restsil, returning the JSON envelope.

    Raises on non-200 / unparseable JSON so the tenacity retry can intercept
    transient transport / timeout errors. The caller (``_one_page``) wraps
    this and converts the remaining failure modes into ``(offset, None)``.
    """
    url = f"{settings.ingestor_base_url_restsil}{path}"
    response = await client.get(url, params=params)
    if response.status_code != 200:
        raise CongresoAPIError(
            f"HTTP {response.status_code} from {url}",
            status_code=response.status_code,
            url=url,
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise CongresoParseError(f"JSON parse error from {url}: {exc}") from exc


async def _one_page(
    semaphore: asyncio.Semaphore,
    client: Any,
    path: str,
    params: dict[str, Any],
    offset: int,
) -> tuple[int, dict[str, Any] | None]:
    """Bounded-concurrency wrapper around ``_afetch_page``.

    Returns ``(offset, envelope)``; on any caught exception returns
    ``(offset, None)`` so a missing page doesn't stall the whole batch.
    """
    async with semaphore:
        try:
            envelope = await _afetch_page(client, path, params)
            return offset, envelope
        except Exception:
            logger.exception(
                "restsil paged fetch failed: path=%s offset=%d", path, offset
            )
            return offset, None


async def afetch_votes_pages(
    offsets: list[int],
    *,
    page_size: int,
    boletin: str | None = None,
    concurrency: int | None = None,
) -> list[tuple[int, dict[str, Any] | None]]:
    """Fetch the given ``buscarVotaciones`` page offsets in parallel.

    Order of returned pairs matches ``offsets`` (desc-by-HORA when offsets
    are increasing, which is what :meth:`iter_votes_desc` produces).
    """
    if not offsets:
        return []
    sem = asyncio.Semaphore(concurrency or settings.ingestor_restsil_async_concurrency)
    async with _async_client() as client:
        coros = []
        for offset in offsets:
            params: dict[str, Any] = {
                "order": "desc",
                "sort": "HORA",
                "limit": page_size,
                "offset": offset,
            }
            if boletin is not None:
                params["boletin"] = boletin
            coros.append(
                _one_page(sem, client, "votaciones/buscarVotaciones", params, offset)
            )
        results = await asyncio.gather(*coros)
    succeeded = sum(1 for _, env in results if env is not None)
    logger.info(
        "Parallel-fetched %d restsil vote pages (%d succeeded)",
        len(offsets),
        succeeded,
    )
    return list(results)


async def afetch_bills_pages(
    offsets: list[int],
    *,
    page_size: int,
    filters: dict[str, Any] | None = None,
    concurrency: int | None = None,
) -> list[tuple[int, dict[str, Any] | None]]:
    """Fetch the given ``buscarProyectosDeLey`` page offsets in parallel.

    ``filters`` accepts the same server-side filter kwargs as
    :meth:`RestsilSenadoClient.search_bills` (``fecha_desde``,
    ``fecha_hasta``, ``estado``, ``camara``, ``iniciativa``, ``boletin``).
    Order of returned pairs matches ``offsets``.
    """
    if not offsets:
        return []
    sem = asyncio.Semaphore(concurrency or settings.ingestor_restsil_async_concurrency)
    base_params: dict[str, Any] = {"order": "desc", "limit": page_size}
    if filters:
        for key in (
            "fecha_desde",
            "fecha_hasta",
            "estado",
            "camara",
            "iniciativa",
            "boletin",
        ):
            value = filters.get(key)
            if value is not None:
                base_params[key] = value
    async with _async_client() as client:
        coros = []
        for offset in offsets:
            params = {**base_params, "offset": offset}
            coros.append(
                _one_page(sem, client, "proyectos/buscarProyectosDeLey", params, offset)
            )
        results = await asyncio.gather(*coros)
    succeeded = sum(1 for _, env in results if env is not None)
    logger.info(
        "Parallel-fetched %d restsil bill pages (%d succeeded)",
        len(offsets),
        succeeded,
    )
    return list(results)
