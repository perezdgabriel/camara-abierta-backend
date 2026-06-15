# BCN REST `ObtenerParlamentariosActivos` is the active-roster source for both chambers

**Status:** Superseded by [ADR-0012](0012-legislator-ingest-pipeline.md) on 2026-06-15 — consolidated with ADR-0001, ADR-0002, ADR-0003, and ADR-0005 into a single legislator-ingest ADR. Content preserved below for history.

Supersedes the roster half of [ADR-0005](0005-bcn-as-senator-roster-source.md)
and the district half of [ADR-0003](0003-scrape-camara-for-deputy-district.md).

`datos.bcn.cl/sparql` had been returning frequent 502s and the SPARQL roster
query (`BCNClient.get_active_appointments`) was the sole driver of the senate
upsert path in `run_ingest_legislators`. Every degraded run therefore silently
dropped senators while leaving deputies (sourced from OpenData) intact —
a slow, asymmetric data outage that was hard to spot.

We were not using a separate BCN REST endpoint that returns the same active
roster reliably:

```
GET https://datos.bcn.cl/catalogo/servicio/ServiciosWebHistoriaDeLaLey/ObtenerParlamentariosActivos
```

One XML payload, 205 `<Parlamentario>` records, partitioned by `Camara/Id`
into 155 deputies (`288`) and 50 senators (`261`). The bridge id
`IdEnCamaraDeOrigen` matches the OpenData deputy `Id` for deputies and the
senado.cl `ID_PARLAMENTARIO` for senators — verified live (2026-06-12)
matching Alejandra Sepúlveda Orbenes: BCN `IdEnCamaraDeOrigen=1341` ↔ senado
`ID_PARLAMENTARIO=1341`. The endpoint also exposes the
deputy→district link
(`RepresentacionGeografica/DivisionPoliticoAdministrativa[@tipo="Distrito"]`),
which was the sole reason ADR-0003 introduced the camara.cl Playwright
scraper.

We make BCN REST the active-roster authority for both chambers and demote
BCN SPARQL to best-effort enrichment. The camara.cl scraper is narrowed to
photo + profile URL only.

## Decisions

- **Roster.** `BCNRestClient.get_active_parliamentarians()` drives the active
  set for both chambers in one call. `is_active=True` is intrinsic to the
  REST response (every record is currently seated). Replaces the BCN SPARQL
  `get_active_appointments` call on the critical path.
- **Deputy district.** `RepresentacionGeografica/.../@tipo="Distrito"` is
  authoritative; `LegislatorParser.parse_bcn_rest_deputy` parses the integer
  out of `"Distrito N° 8"`. The camara.cl scraper no longer touches district.
- **Senator metadata.** `SenadoWebClient.get_full_catalog()` continues to
  supply fields BCN REST omits — gender (`SEXO`), phone, photo URLs, and a
  slug-derived profile URL — joined by `IdEnCamaraDeOrigen` ↔
  `ID_PARLAMENTARIO`. Its `vigentes=1` hemicycle filter is *not* required
  because BCN REST already authoritatively drives `is_active`.
- **Deputy metadata.** OpenData Cámara continues to supply gender and
  `_militancias` (party history backfill into `LegislatorTerm`) — fields BCN
  REST omits — joined by `IdEnCamaraDeOrigen` ↔ OpenData `Id`. ADR-0001
  (OpenData as the party source of truth) is unchanged.
- **Scraper.** `app/scrapers/camara_diputados.py` keeps the camoufox/stealth
  engine but its enrichment payload narrows to `photo_url + profile_url`.
  We retained the scraper rather than dropping it because BCN REST has no
  photo for either chamber and losing deputy avatars would be a visible UX
  regression; replacing it cleanly is a follow-up.
- **BCN SPARQL.** Demoted to best-effort enrichment. Calls to
  `fetch_person_profiles_parallel` (profession, twitter) and
  `fetch_person_appointments_parallel` (`ParliamentaryAppointment` history)
  are wrapped in try/except inside `run_ingest_legislators`. Outages log a
  warning and the rest of the pipeline proceeds. `bcn_uri` and
  `bcn_wiki_url` come from BCN REST directly (the `<Parlamentario @uri>`
  attribute and the `IdWiki` slug, via
  `https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki/{IdWiki}`),
  so those columns no longer depend on SPARQL availability.

## Considered Options

- **Keep ADR-0005 (BCN SPARQL roster) and just retry harder** — rejected:
  the 502 frequency exceeds what any retry budget can paper over, and the
  failure mode is silent partial outage rather than loud error.
- **Run senado.cl `api/hemicycle?vigentes=1` as a parallel senator roster
  alongside BCN SPARQL** — rejected: adds redundancy without solving the
  cross-chamber problem (deputies also lost BCN-derived `is_active` and
  enrichment), and the user's own framing collapsed to a single source once
  it became clear `ObtenerParlamentariosActivos` covers both chambers.
- **Drop the camara.cl scraper entirely** — rejected: BCN REST has no
  deputy photo or profile URL; dropping the scraper without a replacement
  source for those fields is a visible UX regression we are not willing to
  accept yet.
- **Drop BCN SPARQL entirely** — rejected: profession, twitter, and the
  `ParliamentaryAppointment` history table have no other source. Once
  SPARQL is off the critical path, keeping it as best-effort enrichment
  costs almost nothing.

## Consequences

- `run_ingest_legislators` no longer fails on BCN SPARQL outages. The
  critical-path roster fetch is `BCNRestClient`, which has been stable in
  testing; if it ever does fail, the function aborts loudly (returning
  `errors=1` and zero dispatches) rather than partially upserting.
- Vote reconciliation is unchanged. `Legislator.bcn_id` continues to use
  `senado:{PARLID}` / `camara:{OpenDataId}`; BCN REST's `IdEnCamaraDeOrigen`
  is the same integer.
- The `ParliamentaryAppointment` table stops getting new rows during BCN
  SPARQL outages. Historical rows persist. This is an acceptable
  degradation since the table is rendered as supplementary term history,
  not a critical-path data set.
- ADR-0002 stays superseded by ADR-0005 (and by extension by this ADR);
  ADR-0005 stays referenced for the rationale on why senado.cl `vigentes=1`
  was not chosen as a parallel roster source.
