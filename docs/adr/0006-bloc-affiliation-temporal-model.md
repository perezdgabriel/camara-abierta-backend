# Bloc affiliation is modeled as temporal data, not a static label

**Status:** Superseded by [ADR-0014](0014-bloc-affiliation-and-inferred-lean.md) on 2026-06-15 — consolidated with ADR-0007 into a single bloc-model ADR. Content preserved below for history.

To power the majority simulator on `/legisladores`, every party (and every
independent legislator) needs a structural bloc — *oficialismo* or *oposición*.
This alignment is editorial: no congress API exposes it. We model it as a dated
`BlocAffiliation` table (one row per `party_id` + `start_date`, with a nullable
`end_date`) plus a nullable `Legislator.default_bloc` override for independents,
rather than as a single `bloc` column on `PoliticalParty` or a hardcoded
frontend constant. The API exposes only the *current* row
(`PoliticalParty.current_bloc`); the temporal shape is latent in v1.

## Considered Options

- **Hardcoded frontend constant** (`{ "PS": "oficialismo", ... }`) — cheapest,
  and the simulator is the only consumer today. Rejected: bloc is data the
  platform should own (it unlocks the deferred *consenso atípico* signal, which
  the web `CONTEXT.md` already defines but defers precisely because it needs
  "adscripción a bloque por fecha"), and a frontend constant cannot be queried
  server-side or edited without a deploy.
- **Static `bloc` column on `PoliticalParty`** — simpler than a table, and v1
  only reads "current". Rejected: a change of government reassigns blocs
  wholesale, and overwriting the column erases the prior alignment. Any
  historical or as-of-date analysis (the whole point of *consenso atípico*)
  would be impossible. The marginal cost of a dated table now is small; the cost
  of backfilling history later is not.
- **Repurpose the existing `Coalition` / `CoalitionMembership` tables** —
  rejected: the web glossary deliberately distinguishes *coalición* (a formal
  electoral alliance) from *bloque* (the oficialismo/oposición structure) and
  says to avoid conflating them. Overloading `Coalition` would muddy that
  boundary.

## Consequences

- Schema, the `current_bloc` read path, sync version, seed data, and the
  SQLAdmin surface all bake in the temporal shape — reversing it later is a
  migration plus an API contract change, hence this record.
- v1 reads only the active row. A future reader will see a dated table feeding a
  feature that only ever shows "today" and wonder why; the answer is the
  *consenso atípico* roadmap and as-of-date scenario replay.
- The mapping is hand-curated. There is no ingestor; bloc rows are seeded and
  maintained via the admin panel. When the government changes, close the current
  rows (`end_date`) and open new ones (`upsert_bloc_affiliation`).
- `current_bloc` is a Python property reading the `bloc_affiliations`
  relationship; callers must `selectinload` it (done in the legislators service
  and the parties reference endpoint) to avoid N+1.
