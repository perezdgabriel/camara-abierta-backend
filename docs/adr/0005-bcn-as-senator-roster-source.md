# BCN linked data is the source of truth for the parliamentary roster

**Status:** Superseded by [ADR-0012](0012-legislator-ingest-pipeline.md) on 2026-06-15 — chronic BCN SPARQL 502s moved the roster authority to the BCN REST `ObtenerParlamentariosActivos` endpoint; SPARQL biographic enrichment is now operator-triggered via `python -m app.cli ingestors bcn-sparql-enrichment`. Content preserved below for history.

Supersedes [ADR-0002](0002-senado-web-json-as-senator-roster-source.md).

Live testing on 2026-05-28 showed that the senado.cl `web-back` `api/hemicycle`
seated set ADR-0002 adopted is itself stale — at least one senator no longer
holding office is still in `data.hemiciclo`, and replacements have not propagated.
Both senado.cl entry points we have tried (the wspublico `senadores_vigentes.php`
XML and the SPA's JSON `hemicycle`) are therefore unreliable as a roster source.

BCN's linked-open-data endpoint (`https://datos.bcn.cl/sparql`) records each
parliamentary appointment as a dated `bcnbio:PositionPeriod` node with a real
`bcnbio:hasEnd → bcnbio:originalDate`. Filtering by `STR(?fechaFin) >= today`
yields the legally-seated set without depending on a hand-maintained
"seated senators" list. The graph also exposes the bridge IDs we already use
as `bcn_id`: `bcnbio:idSenado` on senator URIs (matches the wspublico/senado.cl
`PARLID`) and `bcnbio:idCamaraDeDiputados` on deputy URIs (matches the OpenData
`Id`). Vote reconciliation (`senado:{PARLID}`, `camara:{id}`) is therefore
preserved unchanged.

We therefore make BCN the source of truth for *which* legislators are currently
seated and for biographic enrichment (profession, twitter, BCN wiki page,
photo). The senado.cl JSON catalog is demoted to a metadata lookup keyed by
`PARLID` for circumscription, region, party abbreviation, email and phone.
OpenData Cámara remains the source of truth for deputy identity and party
records ([ADR-0001](0001-opendata-as-party-source-of-truth.md) unchanged), with
camara.cl scraping continuing to supply district + photo
([ADR-0003](0003-scrape-camara-for-deputy-district.md) unchanged).

## Considered Options

- **Keep ADR-0002 (senado.cl `api/hemicycle` as roster)** — rejected: the
  seated set itself is stale, which is the symptom we observed.
- **Name-match BCN → senado catalog** to keep `senado:{PARLID}` reconciliation
  without relying on a BCN-side ID — rejected: name matching across Spanish
  accents, initials, and married-name conventions is fragile. `bcnbio:idSenado`
  exists, so we can join cleanly without it.
- **BCN over a single fat SPARQL query** with all OPTIONAL profile clauses
  inlined — rejected: a re-elected legislator legitimately has multiple
  `PositionPeriod` nodes, so the cartesian-product risk inflates row counts
  and a single timeout kills the whole pipeline. Two-pass (roster query
  followed by concurrent per-URI enrichment) mirrors the existing
  `fetch_bills_parallel` pattern and isolates per-person failures.
- **BCN SPARQL was previously rejected in ADR-0002** ("text/CONTAINS queries
  time out against ~53M triples") — that rejection was scoped to text matching.
  The appointment-graph traversal here does no text search and is bounded to
  ~205 person nodes, so the timeout argument does not apply.

## Consequences

- We now depend on the availability of `datos.bcn.cl/sparql` for legislator
  ingest to succeed. `DatabaseTask` retry semantics absorb transient failures;
  a count assertion in verification (expect ~50 active senators, ~155 active
  deputies) catches structural regressions.
- `is_active` is now derivable from data for both chambers (`hasEnd >= today`)
  rather than depending on either an upstream `Estado` flag or a hand-curated
  hemicycle list.
- The senado.cl JSON catalog still must be fetched per run for circumscription,
  region, party abbreviation, email, and phone. `SenadoWebClient.get_senators()`
  is renamed to `get_full_catalog()` and the hemiciclo seated-set filter is
  dropped.
- The BCN graph also covers biographic enrichment fields (profession,
  twitter handle, BCN wiki page URL, photo, surname father/mother, gender) that
  no chamber API exposed, allowing deputy profiles to be enriched without
  scraping.
- Two new columns on `Legislator` — `bcn_uri` (canonical re-query handle) and
  `bcn_wiki_url` (the `bcnbio:bcnPage` link) — and two on `LegislatorTerm` —
  `bcn_appointment_uri` (upsert key) and `chamber_type` (since a legislator
  can have past terms in a different chamber) — are added in this pre-release
  schema regeneration.
