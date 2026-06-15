import asyncio
import logging
from typing import Any
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]

from app.core.config import settings
from app.ingestors.clients.opendata_camara import NS_BRACE, OpenDataCamaraClient

logger = logging.getLogger(__name__)

BASE_URL = settings.ingestor_base_url_opendata_camara
MAX_CONCURRENCY = 10


async def fetch_bill_details_parallel(
    bulletins: list[str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[tuple[str, dict[str, Any] | None]]:
    """Fetch full bill details for multiple bulletin numbers in parallel."""
    if not bulletins:
        return []

    parser = OpenDataCamaraClient()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(
        bulletin: str, client: Any
    ) -> tuple[str, dict[str, Any] | None]:
        async with semaphore:
            return await _afetch_and_parse_bill_detail(client, parser, bulletin)

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
        tasks = [fetch_one(bulletin, client) for bulletin in bulletins]
        results = await asyncio.gather(*tasks)

    logger.info(
        "Parallel-fetched %d bill details (%d succeeded)",
        len(bulletins),
        sum(1 for _, result in results if result is not None),
    )
    return list(results)


async def _afetch_and_parse_bill_detail(
    client: Any,
    parser: OpenDataCamaraClient,
    bulletin: str,
) -> tuple[str, dict[str, Any] | None]:
    url = f"{BASE_URL}WSLegislativo.asmx/retornarProyectoLey"

    try:
        response = await client.get(url, params={"prmNumeroBoletin": bulletin})
        if response.status_code != 200:
            logger.warning(
                "HTTP %d fetching bill detail for bulletin %s",
                response.status_code,
                bulletin,
            )
            return bulletin, None

        root = fromstring(response.content)
        proyecto = root
        if proyecto.tag not in (f"{NS_BRACE}ProyectoLey", "ProyectoLey"):
            found = parser._find(root, "ProyectoLey")
            if found is None:
                return bulletin, None
            proyecto = found

        detail = parser._parse_bill_detail(proyecto)
        if not detail.get("bulletin_number"):
            return bulletin, None

        return bulletin, detail
    except ET.ParseError:
        logger.exception("XML parse error for bill detail bulletin %s", bulletin)
        return bulletin, None
    except Exception:
        logger.exception("Failed to fetch bill detail for bulletin %s", bulletin)
        return bulletin, None


async def fetch_voting_details_parallel(
    voting_ids: list[int],
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[tuple[int, dict[str, Any] | None]]:
    """Fetch full Chamber vote details for multiple voting ids in parallel."""
    if not voting_ids:
        return []

    parser = OpenDataCamaraClient()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(
        voting_id: int, client: Any
    ) -> tuple[int, dict[str, Any] | None]:
        async with semaphore:
            return await _afetch_and_parse_vote_detail(client, parser, voting_id)

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
        tasks = [fetch_one(voting_id, client) for voting_id in voting_ids]
        results = await asyncio.gather(*tasks)

    logger.info(
        "Parallel-fetched %d Chamber vote details (%d succeeded)",
        len(voting_ids),
        sum(1 for _, result in results if result is not None),
    )
    return list(results)


async def _afetch_and_parse_vote_detail(
    client: Any,
    parser: OpenDataCamaraClient,
    voting_id: int,
) -> tuple[int, dict[str, Any] | None]:
    url = f"{BASE_URL}WSLegislativo.asmx/retornarVotacionDetalle"

    try:
        response = await client.get(url, params={"prmVotacionId": str(voting_id)})
        if response.status_code != 200:
            logger.warning(
                "HTTP %d fetching Chamber voting id %s",
                response.status_code,
                voting_id,
            )
            return voting_id, None

        root = fromstring(response.content)
        voting = root
        if voting.tag not in (f"{NS_BRACE}Votacion", "Votacion"):
            found = parser._find(root, "Votacion")
            if found is None:
                return voting_id, None
            voting = found

        detail = parser._parse_vote_detail(voting)
        if not detail.get("id"):
            return voting_id, None

        return voting_id, detail
    except ET.ParseError:
        logger.exception("XML parse error for Chamber voting id %s", voting_id)
        return voting_id, None
    except Exception:
        logger.exception("Failed to fetch Chamber voting id %s", voting_id)
        return voting_id, None


async def fetch_chamber_vote_summaries_parallel(
    bulletins: list[str],
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Per-bulletin rich-vote-summary fan-out for chamber-votes ingest (ADR-0013).

    Returns ``(bulletin, rich_summaries)`` for each input bulletin. Failures
    map to an empty list so the caller can degrade gracefully.
    """
    if not bulletins:
        return []

    parser = OpenDataCamaraClient()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(bulletin: str, client: Any) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            return await _afetch_and_parse_bulletin_vote_summaries(
                client, parser, bulletin
            )

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
        tasks = [fetch_one(bulletin, client) for bulletin in bulletins]
        results = await asyncio.gather(*tasks)

    logger.info(
        "Parallel-fetched chamber vote summaries for %d bulletins (%d with data)",
        len(bulletins),
        sum(1 for _, rows in results if rows),
    )
    return list(results)


async def _afetch_and_parse_bulletin_vote_summaries(
    client: Any,
    parser: OpenDataCamaraClient,
    bulletin: str,
) -> tuple[str, list[dict[str, Any]]]:
    url = f"{BASE_URL}WSLegislativo.asmx/retornarVotacionesXProyectoLey"

    try:
        response = await client.get(url, params={"prmNumeroBoletin": bulletin})
        if response.status_code != 200:
            logger.warning(
                "HTTP %d fetching chamber-vote summaries for bulletin %s",
                response.status_code,
                bulletin,
            )
            return bulletin, []

        root = fromstring(response.content)
        rows = [
            parser._parse_chamber_vote_summary(voting)
            for voting in parser._iter(root, "VotacionProyectoLey")
        ]
        return bulletin, rows
    except ET.ParseError:
        logger.exception(
            "XML parse error for chamber-vote summaries bulletin %s", bulletin
        )
        return bulletin, []
    except Exception:
        logger.exception(
            "Failed to fetch chamber-vote summaries for bulletin %s", bulletin
        )
        return bulletin, []
