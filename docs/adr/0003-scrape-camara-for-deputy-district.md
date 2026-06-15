# Deputy → district is scraped from camara.cl (enrichment-only)

**Status:** Superseded by [ADR-0012](0012-legislator-ingest-pipeline.md) on 2026-06-15 — district now comes from the BCN REST roster; the camara.cl scraper is narrowed to deputy photo + profile_url enrichment only. Content preserved below for history.

No congress API exposes which district a deputy represents: `retornarDiputadosPeriodoActual`,
`retornarDiputado`, `retornarDiputadosXPeriodo` (OpenData) and `getDiputados_Vigentes`
(congreso.cl) all omit `<Distrito>`, and BCN SPARQL is too slow/fragile to rely on. The only
source that carries the link is the official roster at `www.camara.cl/diputados/diputados.aspx`,
which is Cloudflare-protected and only reachable via the stealth `ScraperEngine`
(camoufox/patchright). We therefore add `app/scrapers/camara_diputados.py` to scrape it.

The scrape is **enrichment-only**: it matches existing deputies by `camara:{dipid}` and updates
only `district_id` (plus photo/profile_url) via `enrich_legislator_profile`, never creating
legislators or writing party data. This keeps OpenData as the authoritative source for deputy
identity and party (ADR-0001) and avoids the full-upsert hazard of nulling party. It runs as a
separate data collector (`python -m app.cli scrapers diputados`) after the OpenData deputy
ingest.

## Consequences

- camara.cl `prmID` equals the OpenData `Id` behind `camara:{id}` (verified 2026-05-27:
  prmID 803 = René Alinco = `camara:803`), so enrichment matches cleanly. If the scheme ever
  diverges, zero deputies match and the scraper logs it loudly (unmatched count).
- The roster renders 155 `<article class="grid-2">` cards, each with a `prmID` link and a
  `<p>Distrito: N°NN</p>` line. The `SCRAPE_JS` selectors target that structure; a roster
  redesign would break them. A full live run extracts all 155 deputies with district + photo.
- camoufox is required to clear Cloudflare; the chromium `playwright` engine is blocked. The
  shared `launch_camoufox` was fixed for camoufox 0.4.11 (pass a `Screen` object; drop the
  unsupported `timezone` kwarg).
