# Bloc affiliation and inferred lean

Consolidates two predecessor ADRs about bloc modeling into a single
current-state design. Supersedes
[ADR-0006](0006-bloc-affiliation-temporal-model.md) and
[ADR-0007](0007-inferred-bloc-lean-is-a-computed-signal.md). Their content
is preserved in-place for history; this ADR is the single source of truth
for how bloc data flows today.

## Context

The majority simulator on `/legisladores` needs to know each
legislator's structural bloc — *oficialismo* or *oposición*. No congress
API exposes it: the alignment is editorial. Two related decisions
shaped the design — how to *store* the editorial fact (ADR-0006), and
how to *complement* it with a behavioural signal derived from voting
records (ADR-0007). They are tightly coupled (the inferred signal must
not corrupt the editorial fact) and are easier to reason about
together.

## Decisions

### Editorial bloc affiliation is temporal data

A dated `BlocAffiliation` table holds one row per
(`party_id`, `start_date`) with a nullable `end_date`. A nullable
`Legislator.default_bloc` override exists for independents (where the
party-level row would not apply). Both are seeded and maintained
through the SQLAdmin panel — no ingestor.

The public API exposes only the *current* row, surfaced as
`PoliticalParty.current_bloc` (a Python property reading the
`bloc_affiliations` relationship). Callers must `selectinload` the
relationship to avoid N+1 — already done in the legislators service and
the parties reference endpoint.

The temporal shape is latent in v1: only the active row is read. It is
preserved because:

- A change of government reassigns blocs wholesale; overwriting a
  static column would erase prior alignment.
- The deferred *consenso atípico* signal (web `CONTEXT.md` already
  defines it) needs *as-of-date* bloc lookups by definition.
- Backfilling history later (after a column-based design) would be a
  schema migration plus an API contract change. Doing it temporal up
  front is much cheaper.

### Inferred bloc lean is a computed signal — strictly separate

A per-legislator *inclinación de voto* + *disciplina partidaria* rate is
precomputed in the `voting_signals` / `VotingWindowAggregate` mould.
The lean is the bloc whose modal vote a legislator matched most often
across the **contested, decisive** voting sessions of the current
legislative period — sessions where oficialismo and oposición took
opposite sides and the legislator voted *for* / *against*. The
discipline rate is how often a party member matched their own party's
modal.

These never write to `default_bloc` or `BlocAffiliation`. The simulator
resolves a legislator's default placement in this order:

```
default_bloc (editorial)  →  party.current_bloc (structural)  →  inferred lean  →  tray
```

Independents are seated by the inferred lean **only when it clears a
confidence margin**; below the margin (or below a minimum sample) the
seat stays *sin alinear*. For a party member the lean is *informational
only* — surfaced as a seat marker and on the detail page, never an
auto-move.

API surface: `voting_lean` object on the legislator summary (the
simulator reads it from the list endpoint), `party_discipline` object on
the detail.

Bloc-at-date for past sessions is **approximated** as the modal *for* /
*against* among a bloc's members at the session date (using
party-at-date via `LegislatorTerm`, the way `compute_quiebre_bloque`
already does) mapped through each party's *current* bloc. Acceptable
within one period; a true as-of-date bloc map is the *consenso atípico*
roadmap and would tighten it.

## Considered options

- **Hardcoded frontend constant** (`{ "PS": "oficialismo", ... }`).
  Rejected (ADR-0006): the platform should own this; it unlocks the
  deferred *consenso atípico* signal and can't be edited without a
  deploy.
- **Static `bloc` column on `PoliticalParty`.** Rejected (ADR-0006):
  a government change reassigns blocs wholesale, and overwriting the
  column erases history. Any as-of-date analysis becomes impossible.
- **Repurpose `Coalition` / `CoalitionMembership`.** Rejected
  (ADR-0006): the web glossary distinguishes *coalición* (formal
  electoral alliance) from *bloque* (oficialismo/oposición structure).
  Overloading muddies the boundary.
- **Fill `default_bloc` with the inferred value.** Rejected (ADR-0007):
  conflates editorial fact with machine estimate, erases the ability to
  show a rationale ("8 de 11"), and obliterates the distinction between
  "we don't know yet" and "editorially unaligned".
- **Compute lean only in the frontend.** Rejected (ADR-0007): platform
  should own it (reused on the detail page, stepping stone to
  *consenso atípico*), and shipping vote-level data to the browser to
  recompute on every load is wasteful.
- **Recent-window lean instead of whole period.** Deferred for v1
  (ADR-0007): two windows would double the stored surface and the
  simulator popover could disagree with the detail page; a weak
  whole-period lean stays *sin alinear* (safe default) rather than a
  confident mis-seat. Revisit with recency weighting if mid-period
  realignments prove common.
- **Auto-seat party members on their lean (pre-split low-cohesion
  parties).** Rejected (ADR-0007): makes the default scenario reflect
  past behaviour, but the simulator is hypothetical. Turns *Defector*
  (a user move) into a load-time state. Cross-pressured-member story
  is told with a marker and on the detail page instead.

## Consequences

- **Schema, sync version, seed data, and SQLAdmin surface bake in the
  temporal shape.** Reversing it later is a migration plus an API
  contract change.
- **v1 reads only the active row.** A future reader sees a dated table
  feeding a feature that only shows "today" and may wonder why; the
  answer is the *consenso atípico* roadmap and as-of-date scenario
  replay.
- **Bloc-at-date is approximated** for the inferred-lean computation.
  Acceptable within one period; a true as-of-date bloc map is the
  *consenso atípico* roadmap.
- **New API surface:** `voting_lean` on legislator summary,
  `party_discipline` on detail. Shape changes are contract changes.
- **Thresholds (minimum contested sessions, seat margin) are documented
  constants** alongside the `voting_signals` thresholds. Tuning them
  re-seeds simulator defaults.
- **`Disciplina partidaria` ≠ `Cohesión de bloque`.** The new
  per-legislator over-the-period rate must not be confused with the
  existing per-session per-party cohesion, nor read as the pejorative
  *"indisciplina"* the web glossary avoids for a one-off *quiebre de
  bloque*. The web `CONTEXT.md` records the distinction.
- **Operations:** when the government changes, close the current
  `BlocAffiliation` rows (`end_date`) and open new ones via
  `upsert_bloc_affiliation` from the admin panel.
