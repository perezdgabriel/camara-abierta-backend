"""Client for ``restsil.senado.cl/v3/`` — the backend of
``portallegislativo.senado.cl``.

Two endpoints are exposed today:

- ``proyectos/buscarProyectosDeLey`` — paginated bill search, desc-by-entry-
  date by default. Summary fields only (no tramitaciones / etapas / votes).
  Filters: ``fecha_desde``, ``fecha_hasta`` (entry year), ``estado`` (status
  code), ``camara`` (origin chamber), ``iniciativa`` (30 Mensaje / 31 Moción),
  ``boletin``.
- ``votaciones/buscarVotaciones`` — paginated Senate-vote search. Complete
  per-vote payload including SI / NO / ABSTENCION / PAREO per-legislator lists
  with ``PARLID``, ``UUID`` and ``SLUG``. Filter by ``boletin``.

Pagination is **offset-based**: pass ``offset`` and ``limit``. The ``pagina``
query parameter is silently ignored by the upstream; only the response field
of the same name is populated (it is derived from offset + limit).

This is an **unofficial endpoint** scraped from the senado portal — it can be
secured or rate-limited without notice. The legacy ``opendata_camara`` /
``senado`` wspublico paths remain in tree as a failover.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Iterator
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import ConfigurationError, settings
from app.ingestors.clients.base import (
    CongresoAPIError,
    CongresoParseError,
    _httpx,
    _log_retry,
)
from app.ingestors.clients.restsil_senado_async import (
    afetch_bills_pages,
    afetch_votes_pages,
)

logger = logging.getLogger(__name__)


# Bill status codes, per the documented vocabulary on the upstream UI:
#   T → En tramitación   V → Archivado       I → Inadmisible
#   N → Inconstitucional L → Publicado       R → Rechazado   E → Rechazado
BillStatusCode = str  # one of {"T", "V", "I", "N", "L", "R", "E"}
ChamberCode = str  # "S" or "D"


class RestsilSenadoClient:
    """HTTP client for ``restsil.senado.cl/v3/``.

    Use as a context manager so the underlying ``httpx.Client`` is released::

        with RestsilSenadoClient() as restsil:
            for row in restsil.iter_bills_desc(fecha_desde=2026, fecha_hasta=2026):
                ...

    Apikey is read from ``settings.ingestor_restsil_api_key``. Construction
    raises ``ConfigurationError`` if no key is configured — callers that may
    fall back to a legacy source should branch *before* instantiating.
    """

    def __init__(self, timeout: Any = None) -> None:
        if not settings.ingestor_restsil_api_key:
            raise ConfigurationError(
                "INGESTOR_RESTSIL_API_KEY is required when "
                "ingestor_bills_source or ingestor_senate_votes_source is "
                "'restsil'. Set the env var or flip the source flag."
            )
        self._api_key = settings.ingestor_restsil_api_key
        self._base_url = settings.ingestor_base_url_restsil
        self._timeout = timeout
        self._client = None

    @property
    def client(self):  # type: ignore[no-untyped-def]
        httpx = _httpx()
        if self._client is None or self._client.is_closed:
            timeout = self._timeout or httpx.Timeout(
                connect=30.0, read=60.0, write=30.0, pool=30.0
            )
            self._client = httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "CamaraAbierta/1.0 (+https://camaraabierta.cl)",
                    "Accept": "application/json, text/plain, */*",
                    "Authorization": f"Apikey {self._api_key}",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> "RestsilSenadoClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # -- HTTP plumbing ---------------------------------------------------

    @retry(
        retry=retry_if_exception_type(
            (_httpx().TransportError, _httpx().TimeoutException)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        response = self.client.get(url, params=params)
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

    # -- Bills -----------------------------------------------------------

    def search_bills(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        order: str = "desc",
        fecha_desde: int | None = None,
        fecha_hasta: int | None = None,
        estado: BillStatusCode | None = None,
        camara: ChamberCode | None = None,
        iniciativa: int | None = None,
        boletin: str | None = None,
    ) -> dict[str, Any]:
        """Single page of bill summaries.

        Returns the parsed JSON envelope as-is: ``{total, pagina, limite,
        data: [...]}`` (no ``totalPaginas`` / ``offset`` for bills — that
        envelope shape is votes-specific).
        """
        params: dict[str, Any] = {
            "order": order,
            "limit": limit
            if limit is not None
            else settings.ingestor_restsil_page_size,
            "offset": offset,
        }
        if fecha_desde is not None:
            params["fecha_desde"] = fecha_desde
        if fecha_hasta is not None:
            params["fecha_hasta"] = fecha_hasta
        if estado is not None:
            params["estado"] = estado
        if camara is not None:
            params["camara"] = camara
        if iniciativa is not None:
            params["iniciativa"] = iniciativa
        if boletin is not None:
            params["boletin"] = boletin
        return self._get_json("proyectos/buscarProyectosDeLey", params)

    def iter_bills_desc(
        self,
        *,
        page_size: int | None = None,
        max_pages: int | None = None,
        **filters: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield bill summary rows in upstream order (desc-by-entry-date).

        First page is fetched sequentially to obtain ``total`` and to handle
        the common steady-state case (current-year scan ≈ 3 pages) without
        an async hop. When ``total`` indicates more pages, the remainder is
        fanned out in parallel via :func:`afetch_bills_pages` — concurrency
        capped at ``settings.ingestor_restsil_async_concurrency``. The
        ``max_pages`` safety bound covers both the sequential and parallel
        paths combined.

        Per ``ingest_bills_optimizations.md`` the *typical* working set is
        small (~262 rows in the current year, ~7,300 active bills globally),
        so we don't stream inside a page — we yield each ``data`` row one
        at a time and move on to the next envelope.
        """
        limit = page_size or settings.ingestor_restsil_page_size
        cap = max_pages or settings.ingestor_restsil_max_pages_per_tick
        clean_filters = {k: v for k, v in filters.items() if v is not None}

        # --- Page 1 (sequential) ----------------------------------------
        first = self.search_bills(offset=0, limit=limit, **filters)
        rows = first.get("data") or []
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < limit:
            return

        total = int(first.get("total") or 0)
        if total <= limit:
            return

        # --- Remaining pages (parallel) ---------------------------------
        total_pages = math.ceil(total / limit)
        # ``cap`` covers ALL pages combined — page 1 already consumed one.
        remaining = min(total_pages - 1, cap - 1)
        if remaining <= 0:
            return
        offsets = [limit * (i + 1) for i in range(remaining)]
        started = time.monotonic()
        envelopes = asyncio.run(
            afetch_bills_pages(offsets, page_size=limit, filters=clean_filters)
        )
        elapsed = time.monotonic() - started

        for offset, envelope in envelopes:
            if envelope is None:
                # ``afetch_bills_pages`` already logged the exception.
                # We do NOT bail — later offsets are still valid pages.
                continue
            page_rows = envelope.get("data") or []
            for row in page_rows:
                yield row
            if len(page_rows) < limit:
                # Reached the tail of the upstream list within the parallel
                # batch — any later offsets we requested will be empty.
                logger.debug(
                    "iter_bills_desc reached tail at offset=%d (got %d rows)",
                    offset,
                    len(page_rows),
                )
                break

        logger.info(
            "iter_bills_desc fanned out %d pages in %.1fs (filters=%s)",
            remaining,
            elapsed,
            clean_filters,
        )
        if total_pages - 1 > cap - 1:
            logger.warning(
                "iter_bills_desc hit max_pages=%d cap with filters=%s; "
                "remaining rows skipped this tick",
                cap,
                clean_filters,
            )

    # -- Votes -----------------------------------------------------------

    def search_votes(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        order: str = "desc",
        sort: str = "HORA",
        boletin: str | None = None,
    ) -> dict[str, Any]:
        """Single page of Senate votes.

        Returns the parsed JSON envelope: ``{total, pagina, totalPaginas,
        offset, limite, data: [...]}``.
        """
        params: dict[str, Any] = {
            "order": order,
            "sort": sort,
            "limit": limit
            if limit is not None
            else settings.ingestor_restsil_page_size,
            "offset": offset,
        }
        if boletin is not None:
            params["boletin"] = boletin
        return self._get_json("votaciones/buscarVotaciones", params)

    def iter_votes_desc(
        self,
        *,
        page_size: int | None = None,
        max_pages: int | None = None,
        stop_at_id: int | None = None,
        boletin: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield Senate votes in upstream order (desc-by-HORA).

        Stops at the first row whose ``ID_VOTACION <= stop_at_id`` (the
        watermark). The watermark row itself is **not** yielded.

        First page is sequential — it provides ``total`` for fan-out
        planning and handles the common steady-state case (≤ 1 page of new
        votes) without any async overhead. When the watermark wasn't hit
        and ``total`` indicates more pages, the remainder is fanned out in
        parallel via :func:`afetch_votes_pages`. The watermark cutoff still
        applies inside the parallel batch; the over-fetch bound is
        ``concurrency * page_size`` rows past the watermark.

        ``max_pages`` is the combined sequential + parallel safety bound
        (default ``ingestor_restsil_max_pages_per_tick``) so a corrupt or
        wiped watermark cannot ingest the entire upstream history in a
        single tick.
        """
        limit = page_size or settings.ingestor_restsil_page_size
        cap = max_pages or settings.ingestor_restsil_max_pages_per_tick

        # --- Page 1 (sequential) ----------------------------------------
        first = self.search_votes(offset=0, limit=limit, boletin=boletin)
        rows = first.get("data") or []
        if not rows:
            return
        for row in rows:
            if stop_at_id is not None:
                vote_id = row.get("ID_VOTACION")
                if vote_id is not None and int(vote_id) <= stop_at_id:
                    return
            yield row
        if len(rows) < limit:
            return

        total = int(first.get("total") or 0)
        if total <= limit:
            return

        # --- Remaining pages (parallel) ---------------------------------
        total_pages = math.ceil(total / limit)
        remaining = min(total_pages - 1, cap - 1)
        if remaining <= 0:
            return
        offsets = [limit * (i + 1) for i in range(remaining)]
        started = time.monotonic()
        envelopes = asyncio.run(
            afetch_votes_pages(offsets, page_size=limit, boletin=boletin)
        )
        elapsed = time.monotonic() - started

        cutoff_hit = False
        for offset, envelope in envelopes:
            if cutoff_hit:
                break
            if envelope is None:
                continue
            page_rows = envelope.get("data") or []
            for row in page_rows:
                if stop_at_id is not None:
                    vote_id = row.get("ID_VOTACION")
                    if vote_id is not None and int(vote_id) <= stop_at_id:
                        cutoff_hit = True
                        break
                yield row
            if len(page_rows) < limit:
                logger.debug(
                    "iter_votes_desc reached tail at offset=%d (got %d rows)",
                    offset,
                    len(page_rows),
                )
                break

        logger.info(
            "iter_votes_desc fanned out %d pages in %.1fs (stop_at_id=%s)",
            remaining,
            elapsed,
            stop_at_id,
        )
        if not cutoff_hit and total_pages - 1 > cap - 1:
            logger.warning(
                "iter_votes_desc hit max_pages=%d cap (stop_at_id=%s); "
                "remaining older votes skipped this tick",
                cap,
                stop_at_id,
            )
