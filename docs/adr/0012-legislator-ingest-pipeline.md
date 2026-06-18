# Legislator ingest pipeline

Consolidates five predecessor ADRs into the current legislator-ingest design.
Supersedes [ADR-0001](0001-opendata-as-party-source-of-truth.md),
[ADR-0002](0002-senado-web-json-as-senator-roster-source.md),
[ADR-0003](0003-scrape-camara-for-deputy-district.md),
[ADR-0005](0005-bcn-as-senator-roster-source.md), and
[ADR-0011](0011-bcn-rest-as-active-roster-source.md). Their content is
preserved in-place for history; this ADR is the single source of truth for
how the pipeline operates today.

**Partially superseded by [ADR-0015](0015-legislator-identity-and-temporal-terms.md)
on 2026-06-17:** the "Roster authority — BCN REST" section and the
``ParliamentaryAppointment`` paragraph are no longer current. Identity comes
from the historical OpenData + senado.cl pulls; chamber-stint history lives
on ``LegislatorTerm`` (with ``bcn_appointment_uri`` as a nullable column);
BCN REST contributes only ``bcn_uri`` enrichment keyed by the chamber
bridge. See ADR-0015 for the new shape. The rest of this ADR (party
authority, chamber-specific overlays, photo scraper, SPARQL out-of-band
enrichment) remains in force.

## Context

The legislator pipeline has churned through three roster authorities in six
weeks — senado.cl `senadores_vigentes.php` (incomplete: 31/50), senado.cl
`api/hemicycle` (stale seated set), and BCN SPARQL `PositionPeriod` graph
(chronic 502s) — each rejected for a different upstream failure mode. The
party-source decision (OpenData) and the deputy-district decision
(camara.cl scrape) accreted around it as separate ADRs, with partial
supersessions that made the live shape hard to read across five
documents. This ADR collapses them.

## Decisions

### Roster authority — BCN REST

`BCNRestClient.get_active_parliamentarians()` calls
`datos.bcn.cl/catalogo/servicio/ServiciosWebHistoriaDeLaLey/ObtenerParlamentariosActivos`.
One XML GET returns ~205 `<Parlamentario>` records partitioned by
`Camara/Id` (`288` deputies, `261` senators). Every record is currently
seated; `is_active=True` is intrinsic to the response.

`IdEnCamaraDeOrigen` is the bridge id — OpenData deputy `Id` for deputies
and senado.cl `ID_PARLAMENTARIO` for senators — and feeds the `bcn_id`
construction (`camara:{id}` / `senado:{PARLID}`) used by vote
reconciliation. `bcn_uri` (the `<Parlamentario @uri>` attribute) and
`bcn_wiki_url` (derived from `IdWiki` as
`https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki/{IdWiki}`)
are also REST-sourced — they no longer depend on SPARQL.

### Party records — OpenData Cámara is the sole creator

OpenData Cámara `get_diputados_periodo_actual` returns each deputy's
militancia history with full party `Nombre` + `Alias`. Both fields are
required to insert a `PoliticalParty` row: senado.cl provides only an
abbreviation (e.g. `"R.N."`), which is not a usable `name`. Therefore:

- `_upsert_party_from_opendata` is the only writer.
- Senate-side party resolution (`_resolve_party_from_senado`) is a
  case-insensitive abbreviation lookup; it never creates rows. Senators
  without a matching party stay `party_id=None`.
- BCN REST party fields are deliberately discarded by
  `parse_bcn_rest_deputy`: BCN's party name format (e.g. `"Partido
  Renovación Nacional"`) differs from OpenData's (e.g.
  `"Renovación Nacional"`), and routing it through
  `_upsert_party_from_opendata` would collide on the existing
  `abbreviation` unique constraint.

Consequence: deputies must be ingested before senators in each run, and
senators may transiently have no party until OpenData ingest completes.

### Chamber-specific metadata overlays

The REST roster is the base record. Chamber-specific fields are layered on
by joining the bridge id back to chamber sources:

- **Deputies** — overlay OpenData (`OpenDataCamaraClient.get_diputados_periodo_actual`):
  `gender`, `_party_name`, `_party_alias`, `_militancias`. Joined by
  `IdEnCamaraDeOrigen` ↔ OpenData `Id`.
- **Senators** — overlay senado.cl `web-back` JSON catalog
  (`SenadoWebClient.get_full_catalog`): `gender`, `phone`, `photo_url`,
  `photo_thumbnail_url`, `profile_url`, and `_party_name` (which is
  actually the abbreviation senado.cl uses, e.g. `"R.N."` — what
  `_resolve_party_from_senado` looks up). Joined by `IdEnCamaraDeOrigen`
  ↔ `ID_PARLAMENTARIO`.

The senado.cl catalog's `vigentes=1` hemicycle filter is *not* used —
BCN REST already drives `is_active`. The catalog is a metadata lookup
only, not a roster source.

### Deputy photo + profile URL — camara.cl scraper

BCN REST does not expose photos or profile URLs for either chamber.
`app/scrapers/camara_diputados.py` (Playwright via the stealth
`ScraperEngine` because camara.cl is Cloudflare-protected) is narrowed to
photo + profile-URL enrichment only: it matches existing deputies by
`camara:{dipid}` and writes only `photo_url` + `profile_url` via
`enrich_legislator_profile`. It never creates legislators, never touches
party, never touches district. Senator photos come from the senado.cl
catalog (see above), so the scraper is deputy-only.

### BCN biographic enrichment — out-of-band

The legacy SPARQL passes (profession, twitter, gender corroboration,
BCN-sourced photo + `ParliamentaryAppointment` history backfill) live in
a separate CLI command:

```
python -m app.cli ingestors bcn-sparql-enrichment [--dry-run]
```

The function `run_ingest_bcn_sparql_enrichment` re-fetches the BCN REST
roster (cheap, reliable) to get the URI list, then runs the two SPARQL
fan-outs (`fetch_person_profiles_parallel`,
`fetch_person_appointments_parallel`) wrapped in try/except so a SPARQL
outage degrades to a no-op. Operators run it when SPARQL is healthy. The
legislator ingest itself never touches SPARQL — that pain point (silent
senate disappearance during outages) is solved.

## Considered options

- **Senado `senadores_vigentes.php` (wspublico XML) as roster.** Rejected
  in ADR-0002: returns only 31 of the 50 sitting senators.
- **Senado `web-back/api/hemicycle?vigentes=1` as roster.** Rejected in
  ADR-0005: the seated set itself proved stale (replacements lagged for
  weeks).
- **BCN SPARQL `PositionPeriod` graph as roster (`hasEnd >= today`).**
  Adopted in ADR-0005 and rejected in ADR-0011: chronic 502s made senators
  silently disappear during outages because the senate upsert depended on
  the BCN roster to drive the senado.cl catalog join.
- **Name-match BCN → senado catalog.** Rejected (ADR-0005): name matching
  across Spanish accents, initials, and married-name conventions is
  fragile when bridge IDs exist.
- **Senado as party source of truth.** Rejected (ADR-0001): senado.cl
  provides only an abbreviation, no full name; `PoliticalParty.name`
  would be populated with abbreviations.
- **Fuzzy party name matching across OpenData and Senado.** Rejected
  (ADR-0001): abbreviations and full names share no common substring.
- **Drop the camara.cl scraper entirely.** Rejected (ADR-0011): BCN REST
  has no deputy photo or profile URL; dropping the scraper without a
  replacement is a visible UX regression.
- **Drop BCN SPARQL entirely.** Rejected (ADR-0011): profession, twitter,
  and `ParliamentaryAppointment` history have no other source.
  Out-of-band CLI command is the right middle ground.

## Consequences

- **Senate ingest no longer fails silently during BCN SPARQL outages.**
  BCN REST drives the roster; SPARQL is operator-triggered enrichment.
- **Vote reconciliation is unchanged.** `Legislator.bcn_id` continues to
  use `senado:{PARLID}` / `camara:{OpenDataId}`; BCN REST's
  `IdEnCamaraDeOrigen` is the same integer.
- **`bcn_uri` and `bcn_wiki_url` are written on every legislator ingest,
  no SPARQL dependency.**
- **Profession / twitter / `ParliamentaryAppointment` history refresh on
  operator command** (`ingestors bcn-sparql-enrichment`), not as part of
  the scheduled legislator ingest. Existing values persist between runs.
- **camara.cl scraper stays in tree** as the only deputy photo source.
  Drop in a follow-up once a stable replacement is found (e.g., a future
  BCN REST `Foto` field, or a per-deputy fetch off `IdWiki`).
- **Schedule:** `ingestors legislators` runs unchanged; the BCN SPARQL
  command runs out-of-band (no beat entry — operator-triggered).
