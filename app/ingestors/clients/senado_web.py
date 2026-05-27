import logging
from typing import Any

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient, CongresoParseError

logger = logging.getLogger(__name__)


class SenadoWebClient(BaseCongresoClient):
    """Client for the senado.cl SPA backend (``web-back.senado.cl``).

    Unlike :class:`SenadoClient` (the documented wspublico XML API), this hits the
    undocumented JSON API that powers https://www.senado.cl. It is the authoritative
    source for the current senator roster: ``senadores_vigentes.php`` returns only 31
    of the 50 sitting senators, whereas ``/api/hemicycle`` returns all 50 seated IDs
    plus a rich catalog. See ADR-0002.
    """

    BASE_URL = settings.ingestor_base_url_senado_web

    def _get_json(self, url: str, params: dict | None = None) -> Any:
        response = self._get(url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise CongresoParseError(f"JSON parse error from {url}: {exc}") from exc

    def get_senators(self) -> list[dict[str, Any]]:
        """Return the 50 currently-seated senators with full catalog fields.

        ``/api/hemicycle`` returns ``data.hemiciclo`` (nested arrays of seated
        ``ID_PARLAMENTARIO``) and ``data.parlamentarios.data`` (the full catalog of
        all parliamentarians). We flatten the seated IDs and keep only those catalog
        records. ``ID_PARLAMENTARIO`` equals the wspublico ``PARLID``, so downstream
        records reconcile to the existing ``senado:{parlid}`` bcn_id.
        """
        payload = self._get_json("api/hemicycle", params={"limit": 1000})
        data = payload.get("data", {})
        hemiciclo = data.get("hemiciclo", []) or []
        seated_ids = {
            seat
            for block in hemiciclo
            for group in block
            for seat in group
            if isinstance(seat, int)
        }
        catalog = (data.get("parlamentarios", {}) or {}).get("data", []) or []
        senators = [
            record for record in catalog if record.get("ID_PARLAMENTARIO") in seated_ids
        ]
        logger.info(
            "Fetched %d seated senators (of %d catalog records)",
            len(senators),
            len(catalog),
        )
        return senators
