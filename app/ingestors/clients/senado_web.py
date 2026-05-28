import logging
from typing import Any

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient, CongresoParseError

logger = logging.getLogger(__name__)


class SenadoWebClient(BaseCongresoClient):
    """Client for the senado.cl SPA backend (``web-back.senado.cl``).

    Unlike :class:`SenadoClient` (the documented wspublico XML API), this hits
    the undocumented JSON API that powers https://www.senado.cl. As of
    ADR-0005 this client is **no longer the senator roster source** — BCN
    linked data is. We keep it as a *metadata catalog* keyed by
    ``ID_PARLAMENTARIO`` (= wspublico ``PARLID`` = BCN ``bcnbio:idSenado``) for
    fields BCN does not expose: circumscription, region, party abbreviation,
    email, phone, photo.

    The ``data.hemiciclo`` seated set returned by this endpoint is itself
    stale, which is why we no longer filter against it — every catalog row is
    returned and the caller joins by PARLID with BCN's active roster.
    """

    BASE_URL = settings.ingestor_base_url_senado_web

    def _get_json(self, url: str, params: dict | None = None) -> Any:
        response = self._get(url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise CongresoParseError(f"JSON parse error from {url}: {exc}") from exc

    def get_full_catalog(self) -> dict[int, dict[str, Any]]:
        """Return the senate metadata catalog keyed by ``ID_PARLAMENTARIO``.

        ``/api/hemicycle`` returns ``data.parlamentarios.data`` (the full
        catalog of all parliamentarians the senate has on file, seated or
        not). The ``data.hemiciclo`` seated-set filter is intentionally
        skipped: BCN tells us who is currently active; this catalog is used
        purely as a metadata lookup.
        """
        payload = self._get_json("api/hemicycle", params={"limit": 1000})
        data = payload.get("data", {})
        catalog = (data.get("parlamentarios", {}) or {}).get("data", []) or []
        keyed: dict[int, dict[str, Any]] = {}
        for record in catalog:
            parlid = record.get("ID_PARLAMENTARIO")
            if isinstance(parlid, int):
                keyed[parlid] = record
        logger.info(
            "Fetched %d senate catalog records (BCN supplies the active filter)",
            len(keyed),
        )
        return keyed
