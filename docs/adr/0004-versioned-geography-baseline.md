# Geography is a checked-in, versioned baseline loaded manually

The current Chilean electoral geography is effectively static application reference data, not
live ingest data. OpenData Camara's district endpoint is obsolete (60-district map), and the
administrative hierarchy endpoints are stale around current province and commune boundaries.
We therefore stop deriving runtime geography from live APIs and instead check in a versioned
baseline dataset at `app/geography/data/chile_current.json`.

The dataset contains the full current geography needed by the app: regions, provinces,
communes, districts, and circumscriptions, including commune membership for both electoral
maps. It is validated by `app/geography/dataset.py` and applied synchronously through
`python -m app.cli geography`, which calls `apply_geography_dataset` in one transaction and
records the applied dataset version under `IngestorState(entity_type="geography").last_cursor`.

## Considered Options

- **Keep using OpenData for districts and admin hierarchy** — rejected: the district map is
  obsolete and the administrative hierarchy is stale enough to require a patch layer anyway.
- **Build geography at runtime from multiple live sources** — rejected: the composition is
  stable, the source mix is brittle, and live divergence is harder to audit than a checked-in
  baseline.
- **Fetch SERVEL live on every run** — rejected: SERVEL is the authority for re-deriving the
  map, but not needed in the runtime path once the vetted baseline is checked in.

## Consequences

- Geography changes now happen through an explicit dataset update workflow rather than an
  unattended scheduled ingest.
- Operators must run `python -m app.cli geography` (or `just geography`) when setting up or
  refreshing a database; `just seed` now does this before other legislative seed steps.
- The health endpoint keeps the existing last-sync view and now also exposes ingestor cursors,
  so the loaded geography version is visible without querying the database directly.