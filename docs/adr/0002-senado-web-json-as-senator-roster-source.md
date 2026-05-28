# senado.cl web-back JSON API is the authoritative senator roster source

**Status:** Superseded by [ADR-0005](0005-bcn-as-senator-roster-source.md) on 2026-05-28 — the `web-back` `api/hemicycle` seated set is also stale; BCN linked data is now the senator roster source and the senado.cl JSON catalog is demoted to a metadata enrichment lookup keyed by `PARLID`.

The documented Senado wspublico endpoint `senadores_vigentes.php` returns only **31** of the
50 sitting senators, so it cannot back a correct roster. The senado.cl SPA is served by a
JSON API at `web-back.senado.cl`; `GET /api/hemicycle?limit=1000` returns the 50 currently
seated senator IDs (`data.hemiciclo`) plus a rich catalog (party, circumscription, region,
email, phone, gender, photos, slug). Crucially the catalog `ID_PARLAMENTARIO` **equals the
wspublico `PARLID`**, so records reconcile to the existing `senado:{id}` `bcn_id` with no
duplicates. We therefore make this JSON API the sole source for the senator roster
(`SenadoWebClient.get_senators`), replacing the `get_senadores_vigentes` call in
`run_ingest_legislators`. wspublico stays in use for bills, votes, and committees. Party is
still *resolved* by abbreviation and never created (ADR-0001 unchanged), since the catalog
`PARTIDO` field uses the same abbreviation format as wspublico.

## Considered Options

- **Supplement wspublico (keep 31 from XML, add 19 from JSON)** — rejected: two senator code
  paths to keep in sync for no benefit, since the JSON API is a strict superset.
- **Scrape senado.cl HTML** — rejected: the page is a SPA with no useful static HTML; the JSON
  API it calls is far cleaner and needs only httpx (no browser).
- **BCN linked data (datos.bcn.cl SPARQL)** — rejected: reachable but text/CONTAINS queries
  time out against ~53M triples; too slow and fragile.

## Consequences

- We depend on an undocumented endpoint that could change without notice; a schema change
  breaks the senator ingest. Mitigated by the count assertion in verification (expect 50).
- Senator `is_active` now means "present in the hemicycle seated set" rather than an upstream
  `Estado` flag.
