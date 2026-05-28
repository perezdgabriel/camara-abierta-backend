"""SPARQL client for BCN's linked-open-data endpoint (``datos.bcn.cl/sparql``).

BCN is the source of truth for *which* legislators are currently seated and for
biographic enrichment (profession, twitter handle, BCN wiki page, photo). See
ADR-0005. Active senators / deputies are identified via dated
``bcnbio:PositionPeriod`` nodes (``hasEnd >= today``), bypassing the stale
hemicycle/Estado flags exposed by the chamber APIs.

This module exposes:

* :class:`BCNClient` — a synchronous client wrapping the SPARQL endpoint with
  three methods:
    - :meth:`get_active_appointments`: roster query, returns one row per active
      ``PositionPeriod``;
    - :meth:`get_person_profile`: per-URI enrichment;
    - :meth:`get_person_appointments`: all appointments (past + present) for a
      person URI, used to backfill ``LegislatorTerm`` history.

* :func:`fetch_person_profiles_parallel` and
  :func:`fetch_person_appointments_parallel` — asyncio fan-out helpers
  mirroring :func:`app.ingestors.clients.senado_async.fetch_bills_parallel`,
  used by ``run_ingest_legislators`` to enrich ~205 person nodes concurrently.

Two known BCN-side quirks:

* OPTIONAL clauses on multi-valued properties (e.g. a person with several
  ``twitterAccount`` triples) produce duplicate bindings. The extraction
  helpers therefore pick the first value per variable rather than asserting
  uniqueness.

* The spec's roster query uses ``BIND(STR(?cargo) AS ?cargoId)`` +
  ``FILTER(REGEX(?cargoId, "^[12]$"))``, which never matches because
  ``?cargoId`` is bound to the full URI. We strip with
  ``REPLACE(STR(?cargo), ".*/", "")`` and compare with ``IN ("1", "2")``.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
from typing import Any

from app.core.config import settings
from app.ingestors.clients.base import (
    BaseCongresoClient,
    CongresoAPIError,
    CongresoParseError,
)

logger = logging.getLogger(__name__)

USER_AGENT = "CamaraAbierta-Engine/3.0"
SPARQL_ACCEPT = "application/sparql-results+json"
SPARQL_PATH = "sparql"
# BCN's public SPARQL endpoint is shared infrastructure; keep concurrent
# requests modest to stay well under its rate ceiling. 429s we still recover
# from in :func:`_arun_with_retry`.
MAX_CONCURRENCY = 3
# Retry budget for transient BCN errors (429 / 5xx). The endpoint occasionally
# rate-limits even at low concurrency; this gives ~2+4+8+16+32 = ~60s of
# exponential backoff per request before giving up.
MAX_RETRIES = 6
RETRY_AFTER_FALLBACK_SECONDS = 5.0
RETRY_AFTER_CAP_SECONDS = 60.0


def _retry_after_seconds(response: Any) -> float | None:
    """Extract a ``Retry-After`` header as seconds.

    Returns ``None`` if the header is absent, invalid, or names an HTTP-date
    rather than a delta-seconds value (we treat HTTP-date Retry-After as
    unparseable and fall back to exponential backoff).
    """
    raw = response.headers.get("retry-after") if response is not None else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except TypeError, ValueError:
        return None
    return min(seconds, RETRY_AFTER_CAP_SECONDS)


def _bcn_backoff(attempt: int) -> float:
    """Capped exponential backoff with jitter for BCN retries."""
    base = RETRY_AFTER_FALLBACK_SECONDS * (2 ** (attempt - 1))
    return min(base, RETRY_AFTER_CAP_SECONDS) + random.uniform(0, 1)


CARGO_DEPUTY = "1"
CARGO_SENATOR = "2"


_ROSTER_QUERY = """
PREFIX bcnbio: <http://datos.bcn.cl/ontologies/bcn-biographies#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>

SELECT DISTINCT ?personUri ?appointmentUri ?cargoId ?idSenado ?idCamara ?nombre ?fechaInicio ?fechaFin
WHERE {
  ?personUri a foaf:Person .
  ?personUri foaf:name ?nombre .

  ?personUri bcnbio:hasParliamentaryAppointment ?appointmentUri .
  ?appointmentUri bcnbio:hasPosition ?cargo .
  BIND(REPLACE(STR(?cargo), ".*/", "") AS ?cargoId)
  FILTER(?cargoId IN ("1", "2"))

  ?appointmentUri bcnbio:hasBeginning ?nodoInicio .
  ?nodoInicio bcnbio:originalDate ?fechaInicio .

  ?appointmentUri bcnbio:hasEnd ?nodoFin .
  ?nodoFin bcnbio:originalDate ?fechaFin .

  FILTER(STR(?fechaFin) >= "%(today)s")

  OPTIONAL { ?personUri bcnbio:idSenado ?idSenado . }
  OPTIONAL { ?personUri bcnbio:idCamaraDeDiputados ?idCamara . }
}
ORDER BY ?cargoId ?nombre
"""


_PROFILE_QUERY = """
PREFIX bcnbio: <http://datos.bcn.cl/ontologies/bcn-biographies#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?nombre ?prefLabel ?profesion ?imagen ?thumbnail ?twitter ?paginaWiki ?genero ?idSenado ?idCamara
WHERE {
  <%(uri)s> foaf:name ?nombre .
  OPTIONAL { <%(uri)s> skos:prefLabel ?prefLabel . }
  OPTIONAL { <%(uri)s> bcnbio:profession ?profesion . }
  OPTIONAL { <%(uri)s> foaf:depiction ?imagen . }
  OPTIONAL { <%(uri)s> foaf:thumbnail ?thumbnail . }
  OPTIONAL { <%(uri)s> bcnbio:twitterAccount ?twitter . }
  OPTIONAL { <%(uri)s> bcnbio:bcnPage ?paginaWiki . }
  OPTIONAL { <%(uri)s> foaf:gender ?genero . }
  OPTIONAL { <%(uri)s> bcnbio:idSenado ?idSenado . }
  OPTIONAL { <%(uri)s> bcnbio:idCamaraDeDiputados ?idCamara . }
}
"""


_APPOINTMENTS_QUERY = """
PREFIX bcnbio: <http://datos.bcn.cl/ontologies/bcn-biographies#>

SELECT DISTINCT ?appointmentUri ?cargoId ?fechaInicio ?fechaFin
WHERE {
  <%(uri)s> bcnbio:hasParliamentaryAppointment ?appointmentUri .
  ?appointmentUri bcnbio:hasPosition ?cargo .
  BIND(REPLACE(STR(?cargo), ".*/", "") AS ?cargoId)
  FILTER(?cargoId IN ("1", "2"))

  ?appointmentUri bcnbio:hasBeginning ?nodoInicio .
  ?nodoInicio bcnbio:originalDate ?fechaInicio .

  ?appointmentUri bcnbio:hasEnd ?nodoFin .
  ?nodoFin bcnbio:originalDate ?fechaFin .
}
ORDER BY ?fechaInicio
"""


def _binding_value(binding: dict[str, Any], key: str) -> str | None:
    """Return the string value of a SPARQL binding, or None if missing/empty."""
    cell = binding.get(key)
    if not cell:
        return None
    value = cell.get("value")
    if value in (None, ""):
        return None
    return value


def _first_value(bindings: list[dict[str, Any]], key: str) -> str | None:
    """Return the first non-empty value of a variable across bindings."""
    for binding in bindings:
        value = _binding_value(binding, key)
        if value is not None:
            return value
    return None


class BCNClient(BaseCongresoClient):
    """Synchronous client for the BCN SPARQL endpoint."""

    BASE_URL = settings.ingestor_base_url_bcn

    @property
    def client(self):
        import httpx

        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": SPARQL_ACCEPT,
                },
            )
        return self._client

    def _sparql(self, query: str) -> list[dict[str, Any]]:
        """Execute a SPARQL query, retrying on 429/5xx, return ``results.bindings``.

        Honors ``Retry-After`` when the server sends one; otherwise falls back
        to capped exponential backoff with jitter. Connection-level errors are
        retried by the base class' tenacity wrapper on :meth:`_get`.
        """
        import httpx

        import time as _time

        full_url = f"{self.BASE_URL}{SPARQL_PATH}"
        params = {"query": query, "format": SPARQL_ACCEPT}

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.client.get(full_url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = _bcn_backoff(attempt)
                logger.warning(
                    "BCN transport error %s (attempt %d/%d) — sleeping %.1fs",
                    exc,
                    attempt,
                    MAX_RETRIES,
                    wait,
                )
                _time.sleep(wait)
                continue

            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise CongresoParseError(
                        f"SPARQL JSON parse error from {SPARQL_PATH}: {exc}"
                    ) from exc
                return payload.get("results", {}).get("bindings", []) or []

            status = response.status_code
            retryable = status == 429 or 500 <= status < 600
            if not retryable or attempt == MAX_RETRIES:
                raise CongresoAPIError(
                    f"HTTP {status} from BCN SPARQL after {attempt} attempt(s)",
                    status_code=status,
                    url=full_url,
                )
            wait = _retry_after_seconds(response) or _bcn_backoff(attempt)
            logger.warning(
                "BCN HTTP %d (attempt %d/%d) — sleeping %.1fs",
                status,
                attempt,
                MAX_RETRIES,
                wait,
            )
            _time.sleep(wait)

        # Defensive: the loop above either returns or raises on the last attempt.
        raise CongresoAPIError(
            f"BCN SPARQL exhausted {MAX_RETRIES} retries",
            url=full_url,
        )

    def get_active_appointments(
        self, today: datetime.date | None = None
    ) -> list[dict[str, Any]]:
        """Return one row per active ``PositionPeriod`` (deputies + senators).

        Each row carries: ``personUri``, ``appointmentUri``, ``cargoId``
        (``"1"`` deputy / ``"2"`` senator), ``idSenado`` and ``idCamara``
        bridge IDs, ``full_name``, ``term_start``, ``term_end``.
        """
        today = today or datetime.date.today()
        query = _ROSTER_QUERY % {"today": today.isoformat()}
        bindings = self._sparql(query)
        rows = [self._extract_roster_row(b) for b in bindings]
        logger.info(
            "BCN roster: %d active appointments as of %s",
            len(rows),
            today.isoformat(),
        )
        return rows

    def get_person_profile(self, person_uri: str) -> dict[str, Any] | None:
        """Return enrichment fields for one person URI, or None if not found."""
        if not person_uri:
            return None
        query = _PROFILE_QUERY % {"uri": person_uri}
        bindings = self._sparql(query)
        if not bindings:
            return None
        return self._extract_profile(person_uri, bindings)

    def get_person_appointments(self, person_uri: str) -> list[dict[str, Any]]:
        """Return every parliamentary appointment (past + present) for a URI."""
        if not person_uri:
            return []
        query = _APPOINTMENTS_QUERY % {"uri": person_uri}
        bindings = self._sparql(query)
        return [self._extract_appointment(b) for b in bindings]

    @staticmethod
    def _extract_roster_row(binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "personUri": _binding_value(binding, "personUri"),
            "appointmentUri": _binding_value(binding, "appointmentUri"),
            "cargoId": _binding_value(binding, "cargoId"),
            "idSenado": _binding_value(binding, "idSenado"),
            "idCamara": _binding_value(binding, "idCamara"),
            "full_name": _binding_value(binding, "nombre"),
            "term_start": _binding_value(binding, "fechaInicio"),
            "term_end": _binding_value(binding, "fechaFin"),
        }

    @staticmethod
    def _extract_profile(
        person_uri: str, bindings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "personUri": person_uri,
            "full_name": _first_value(bindings, "nombre"),
            "pref_label": _first_value(bindings, "prefLabel"),
            "profession": _first_value(bindings, "profesion"),
            "photo_url": _first_value(bindings, "imagen"),
            "photo_thumbnail_url": _first_value(bindings, "thumbnail"),
            "twitter": _first_value(bindings, "twitter"),
            "bcn_wiki_url": _first_value(bindings, "paginaWiki"),
            "gender": _first_value(bindings, "genero"),
            "idSenado": _first_value(bindings, "idSenado"),
            "idCamara": _first_value(bindings, "idCamara"),
        }

    @staticmethod
    def _extract_appointment(binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "appointmentUri": _binding_value(binding, "appointmentUri"),
            "cargoId": _binding_value(binding, "cargoId"),
            "term_start": _binding_value(binding, "fechaInicio"),
            "term_end": _binding_value(binding, "fechaFin"),
        }


async def _afetch_sparql(client: Any, query: str) -> list[dict[str, Any]]:
    """Async SPARQL request with 429/5xx retry + ``Retry-After`` support."""
    import httpx

    url = f"{settings.ingestor_base_url_bcn}{SPARQL_PATH}"
    params = {"query": query, "format": SPARQL_ACCEPT}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.get(url, params=params)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = _bcn_backoff(attempt)
            logger.warning(
                "BCN async transport error %s (attempt %d/%d) — sleeping %.1fs",
                exc,
                attempt,
                MAX_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
            continue

        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise CongresoParseError(f"SPARQL JSON parse error: {exc}") from exc
            return payload.get("results", {}).get("bindings", []) or []

        status = response.status_code
        retryable = status == 429 or 500 <= status < 600
        if not retryable or attempt == MAX_RETRIES:
            raise CongresoAPIError(
                f"HTTP {status} from BCN SPARQL after {attempt} attempt(s)",
                status_code=status,
                url=url,
            )
        wait = _retry_after_seconds(response) or _bcn_backoff(attempt)
        logger.warning(
            "BCN async HTTP %d (attempt %d/%d) — sleeping %.1fs",
            status,
            attempt,
            MAX_RETRIES,
            wait,
        )
        await asyncio.sleep(wait)

    raise CongresoAPIError(
        f"BCN async SPARQL exhausted {MAX_RETRIES} retries",
        url=url,
    )


def _build_async_client():
    import httpx

    timeout = httpx.Timeout(60.0, connect=30.0)
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": SPARQL_ACCEPT,
        },
    )


async def fetch_person_profiles_parallel(
    uris: list[str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> dict[str, dict[str, Any] | None]:
    """Fetch BCN per-URI profiles concurrently. One failure does not fail the run."""
    if not uris:
        return {}

    semaphore = asyncio.Semaphore(max_concurrency)
    unique = list(dict.fromkeys(uris))

    async def fetch_one(client: Any, uri: str) -> tuple[str, dict[str, Any] | None]:
        async with semaphore:
            try:
                bindings = await _afetch_sparql(client, _PROFILE_QUERY % {"uri": uri})
            except Exception:
                logger.exception("BCN profile fetch failed for %s", uri)
                return uri, None
            if not bindings:
                return uri, None
            return uri, BCNClient._extract_profile(uri, bindings)

    async with _build_async_client() as client:
        results = await asyncio.gather(*(fetch_one(client, uri) for uri in unique))

    succeeded = sum(1 for _, profile in results if profile is not None)
    logger.info("BCN profiles: %d of %d fetched", succeeded, len(unique))
    return dict(results)


async def fetch_person_appointments_parallel(
    uris: list[str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch every appointment for each URI concurrently. One failure stays empty."""
    if not uris:
        return {}

    semaphore = asyncio.Semaphore(max_concurrency)
    unique = list(dict.fromkeys(uris))

    async def fetch_one(client: Any, uri: str) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            try:
                bindings = await _afetch_sparql(
                    client, _APPOINTMENTS_QUERY % {"uri": uri}
                )
            except Exception:
                logger.exception("BCN appointments fetch failed for %s", uri)
                return uri, []
            return uri, [BCNClient._extract_appointment(b) for b in bindings]

    async with _build_async_client() as client:
        results = await asyncio.gather(*(fetch_one(client, uri) for uri in unique))

    total = sum(len(v) for _, v in results)
    logger.info("BCN appointments: %d rows across %d URIs", total, len(unique))
    return dict(results)
