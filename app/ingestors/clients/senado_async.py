import asyncio
import logging
from typing import Any
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]

from app.core.config import settings
from app.ingestors.clients.senado import SenadoClient

logger = logging.getLogger(__name__)

BASE_URL = settings.ingestor_base_url_senado
MAX_CONCURRENCY = 10


async def fetch_bills_parallel(
    bulletins: list[str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[tuple[str, dict[str, Any] | None]]:
    """Fetch full bill details for multiple bulletins in parallel via asyncio.

    Returns a list of (bulletin, bill_dict_or_None) in the same order as input.
    """
    if not bulletins:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(
        bulletin: str, client: Any
    ) -> tuple[str, dict[str, Any] | None]:
        async with semaphore:
            return await _afetch_and_parse(client, bulletin)

    import httpx

    timeout = httpx.Timeout(60.0, connect=30.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "CamaraAbierta/1.0 (+https://camaraabierta.cl)",
            "Accept": "application/xml, text/xml, */*",
        },
    ) as client:
        tasks = [fetch_one(bn, client) for bn in bulletins]
        results = await asyncio.gather(*tasks)

    logger.info(
        "Parallel-fetched %d bills (%d succeeded)",
        len(bulletins),
        sum(1 for _, r in results if r is not None),
    )
    return list(results)


async def _afetch_and_parse(
    client: Any, bulletin: str
) -> tuple[str, dict[str, Any] | None]:
    boletin_num = bulletin.split("-")[0]
    url = f"{BASE_URL}tramitacion.php"

    try:
        response = await client.get(url, params={"boletin": boletin_num})
        if response.status_code != 200:
            logger.warning(
                "HTTP %d fetching bulletin %s", response.status_code, bulletin
            )
            return bulletin, None

        root = fromstring(response.content)
        raw = SenadoClient._parse_bill_xml(root, bulletin)
        return bulletin, raw
    except ET.ParseError:
        logger.exception("XML parse error for bulletin %s", bulletin)
        return bulletin, None
    except Exception:
        logger.exception("Failed to fetch bulletin %s", bulletin)
        return bulletin, None


async def fetch_votes_parallel(
    bulletins: list[str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[tuple[str, list[dict[str, Any]] | None]]:
    """Fetch Senate votes for multiple bulletins in parallel via votaciones.php.

    The dedicated ``votaciones.php`` endpoint is the complete source of Senate
    votes — the ``<votacion>`` nodes embedded in ``tramitacion.php`` are often
    absent. Returns ``(bulletin, votes)`` per input; ``votes`` is ``None`` when
    the fetch/parse failed (so callers can fall back) and a list (possibly empty)
    when it succeeded.
    """
    if not bulletins:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(
        bulletin: str, client: Any
    ) -> tuple[str, list[dict[str, Any]] | None]:
        async with semaphore:
            return await _afetch_and_parse_votes(client, bulletin)

    import httpx

    timeout = httpx.Timeout(60.0, connect=30.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "CamaraAbierta/1.0 (+https://camaraabierta.cl)",
            "Accept": "application/xml, text/xml, */*",
        },
    ) as client:
        tasks = [fetch_one(bn, client) for bn in bulletins]
        results = await asyncio.gather(*tasks)

    logger.info(
        "Parallel-fetched votes for %d bulletins (%d succeeded)",
        len(bulletins),
        sum(1 for _, r in results if r is not None),
    )
    return list(results)


async def _afetch_and_parse_votes(
    client: Any, bulletin: str
) -> tuple[str, list[dict[str, Any]] | None]:
    boletin_num = bulletin.split("-")[0]
    url = f"{BASE_URL}votaciones.php"

    try:
        response = await client.get(url, params={"boletin": boletin_num})
        if response.status_code != 200:
            logger.warning(
                "HTTP %d fetching votes for bulletin %s",
                response.status_code,
                bulletin,
            )
            return bulletin, None

        root = fromstring(response.content)
        return bulletin, SenadoClient._parse_votaciones_from_root(root)
    except ET.ParseError:
        logger.exception("XML parse error fetching votes for bulletin %s", bulletin)
        return bulletin, None
    except Exception:
        logger.exception("Failed to fetch votes for bulletin %s", bulletin)
        return bulletin, None
