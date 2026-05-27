from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.session import task_session
from app.scrapers.common import ScraperEngine, jitter_sleep
from app.services.write import enrich_legislator_profile

logger = logging.getLogger(__name__)

HOME_URL = "https://www.camara.cl/"
ROSTER_URL = "https://www.camara.cl/diputados/diputados.aspx"

# www.camara.cl is Cloudflare-protected, so this MUST run through the stealth
# ScraperEngine (camoufox recommended). No congress API exposes the deputy->district
# link, which is why we scrape it here (see ADR-0003).
#
# DOM (verified 2026-05-27): the roster renders each deputy as
#   <article class="grid-2">
#     <a href="detalle/mociones.aspx?prmID=803"><img src="/img.aspx?prmID=GRCL803"></a>
#     <h4><a href="...prmID=803">Sr. René Alinco</a></h4>
#     <p>Distrito: N°27</p>
#     <p>Partido: IND</p>
#   </article>
# `prmID` equals the OpenData `Id` behind the `camara:{id}` bcn_id (e.g. 803 = René
# Alinco). In the browser, img.src / a.href resolve to absolute URLs.
SCRAPE_JS = r"""
() => {
    const out = [];
    document.querySelectorAll('article.grid-2').forEach(card => {
        const link = card.querySelector('a[href*="prmID="], a[href*="prmId="]');
        if (!link) return;
        const m = link.href.match(/prmid=(\d+)/i);
        if (!m) return;
        const text = card.textContent || '';
        const distM = text.match(/Distrito:?\s*N?[°º]?\s*(\d+)/i);
        const img = card.querySelector('img');
        out.push({
            dipid: m[1],
            district: distM ? distM[1] : '',
            photo_url: img ? img.src : '',
            profile_url: link.href,
        });
    });
    return out;
}
"""


def build_enrichment(raw: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Convert a scraped deputy dict into a (bcn_id, fields) enrichment payload.

    Pure function (no I/O) so it can be unit-tested against a fixture. Returns
    ``None`` when the row lacks a usable id.
    """
    dipid = str(raw.get("dipid") or "").strip()
    if not dipid:
        return None
    bcn_id = f"camara:{dipid}"

    fields: dict[str, Any] = {}
    try:
        district = int(str(raw.get("district") or "").strip())
        if district > 0:
            fields["district_number"] = district
    except ValueError, TypeError:
        pass
    if raw.get("photo_url"):
        fields["photo_url"] = str(raw["photo_url"]).strip()
    if raw.get("profile_url"):
        fields["profile_url"] = str(raw["profile_url"]).strip()
    return bcn_id, fields


async def _scrape(engine: str, headed: bool) -> list[dict[str, Any]]:
    async with ScraperEngine(engine=engine, headed=headed) as eng:
        await eng.warm_up(HOME_URL)
        loaded = await eng.goto_with_retry(ROSTER_URL, wait_for="article.grid-2")
        if not loaded:
            logger.warning("Failed to load the deputy roster at %s", ROSTER_URL)
            return []
        await jitter_sleep(1.5, 3.0)
        data = await eng.page.evaluate(SCRAPE_JS)
        return list(data or [])


def run_scrape(
    *,
    engine: str = "camoufox",
    headed: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scrape the Cámara roster and enrich existing deputies with district + photo.

    Enrichment-only: matches existing legislators by ``camara:{dipid}`` and updates
    only district/photo/profile_url via :func:`enrich_legislator_profile`. Never
    creates legislators or touches party data (ADR-0001). Unmatched deputies are
    skipped and counted so an id-scheme mismatch is loud during verification.
    """
    deputies = asyncio.run(_scrape(engine, headed))
    found = len(deputies)
    enriched = 0
    unmatched = 0

    if not dry_run:
        with task_session() as db:
            for raw in deputies:
                payload = build_enrichment(raw)
                if payload is None:
                    continue
                bcn_id, fields = payload
                if not fields:
                    continue
                if enrich_legislator_profile(db, bcn_id, fields) is None:
                    unmatched += 1
                    logger.warning("No legislator matched bcn_id=%s", bcn_id)
                else:
                    enriched += 1

    if unmatched and enriched == 0 and found:
        logger.error(
            "Scraped %d deputies but matched none — prmId likely differs from the "
            "OpenData Id behind camara:{id} bcn_ids; verify the id scheme.",
            found,
        )

    return {
        "found": found,
        "enriched": enriched,
        "unmatched": unmatched,
        "dry_run": dry_run,
        "errors": 0,
    }
