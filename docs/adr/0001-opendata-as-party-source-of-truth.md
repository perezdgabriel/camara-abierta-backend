# OpenData Camara is the authoritative source for PoliticalParty records

**Status:** Superseded by [ADR-0012](0012-legislator-ingest-pipeline.md) on 2026-06-15 — the party-source decision is now part of the consolidated legislator-ingest ADR. Content preserved below for history.

The Senado API returns party data only as a short abbreviation (e.g. `"P.S."`, `"R.N."`), which is not enough to create a valid `PoliticalParty` record. OpenData Camara returns both the full name and a clean alias per militancia. We therefore treat OpenData as the sole source for creating and updating party records, while the Senado path resolves legislators to existing parties by normalized abbreviation and leaves `party_id = null` if no match is found. As a consequence, OpenData deputies must be ingested before Senado senators in each run, and senators may temporarily have no party until the first OpenData ingest completes.

## Considered Options

- **Fuzzy name matching** — normalize and compare both the Senado abbreviation and the OpenData name to detect the same party. Rejected: too fragile; abbreviations and names share no common substring.
- **Senado as source of truth** — create parties from Senado's abbreviation field. Rejected: Senado provides no full name, so the `name` column would be populated with abbreviations, which are user-facing data.
