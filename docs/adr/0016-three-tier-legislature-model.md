# Three-tier legislative model (Período → Legislatura → Sesión)

**Status:** Accepted, 2026-06-21.

## Context

Chilean Congress has three distinct legislative concepts that the app needs
to track:

1. **Período Legislativo** — the 4-year presidential and Chamber-of-Deputies
   cycle (e.g. 2026–2030).
2. **Legislatura** — the 1-year working cycle of Congress, with a sequential
   historical numbering dating to the 19th century (e.g. Legislatura 374 =
   2026–2027). Post-2005 reform every Legislatura is *Ordinaria* and runs
   continuously Mar 11 → Mar 10 next year, broken only by the February receso.
3. **Sesión Legislativa** — a single scheduled meeting in the Sala (Chamber
   floor) or in a Comisión (Committee), classified as *Ordinaria* (regular,
   mandated) or *Especial* (convened outside regular hours).

Before this ADR, the app only modeled two of the three:

- `LegislativePeriod` correctly captured the 4-year cycle.
- `LegislativeSession` was a single table doing double duty for *both* the
  annual Legislatura *and* a single meeting. The parser at
  `app/ingestors/parsers/legislature.py` wrote both `parse_legislature()`
  output and `parse_session()` output into the same table, using a free-string
  `session_type` field with the values `ordinary` / `extraordinary` — which is
  the **Legislatura** vocabulary applied indiscriminately.
- The historical Legislatura number (374) was conflated with an intra-period
  sequence number — it was never stored as a globally unique historical count.
- `VotingSession.session_id` ambiguously pointed at "whatever lived in
  `legislative_sessions`", which in practice meant Legislaturas.
- `CONTEXT.md` had no glossary entry for any of the three terms.

The conflation made several downstream concerns ambiguous. A vote could not
be linked to a specific meeting; the historical Legislatura number could not
be displayed; the `session_type` vocabulary was wrong at the meeting level
(meetings are *Ordinaria* / *Especial*, never *Extraordinaria*); and Sala
versus Comisión was unrepresentable.

## Decisions

### 1. Three-tier model

Introduce `Legislature` as a first-class entity between `LegislativePeriod`
and `LegislativeSession`. Demote `LegislativeSession` to represent a single
meeting only.

| Concept | Model | Cardinality |
|---|---|---|
| Período Legislativo | `LegislativePeriod` | ≈1 every 4 years |
| Legislatura | `Legislature` | ≈1 per year, sequentially numbered (374, 375, …) |
| Sesión Legislativa | `LegislativeSession` | many per Legislatura |

FK chain: `LegislativeSession.legislature_id → Legislature.period_id`.

### 2. Half-open date ranges `[start, end)` on all three tiers

`end_date` is exclusive — equal to the start of the next entity in sequence.
The 2026 period is `[2026-03-11, 2030-03-11)`; Legislatura 374 is
`[2026-03-11, 2027-03-11)`. Display layers may render the human-friendly
inclusive form ("11 mar 2026 – 10 mar 2027") but storage is always exclusive.

`LegislatorTerm` is **not** changed by this ADR — it retains inclusive
`[start, end]` semantics because the 11+ query sites that read it use
`start_date <= d AND end_date >= d`. The two conventions coexist; the comment
block at `app/ingestors/parsers/legislators.py:77` documents the split.

### 3. `Legislature.number` is sourced upstream, never synthesized

OpenData Cámara exposes the historical Legislatura number on
`getLegislaturas`. The ingestor stores that value directly with a unique
constraint. If upstream is silent on a given Legislatura, we wait for it —
synthesizing the number would risk drifting from the official sequence.

### 4. `SessionKind {ordinaria, especial}` enum

Proper Python `Enum` per the CONTEXT.md "Enums" convention, replacing the
free-string `session_type` field. Distinct from `LegislatureKind {ordinaria,
extraordinaria}` — at the meeting level "Especial" means *convened outside
regular hours*, not an extraordinary annual cycle. Upstream payloads that
still emit `"Extraordinaria"` for a meeting are normalized down to `especial`
in the parser.

### 5. Sala vs Comisión via nullable `committee_id`

`LegislativeSession.committee_id` FK to `Committee`, `ondelete=SET NULL`.
Null = Sala (plenary) meeting; non-null = Comisión meeting of that committee.
`chamber_id` is retained because committees themselves belong to one chamber,
so the chamber is meaningful in both Sala and Comisión cases. No separate
`venue` enum — the presence/absence of the FK is self-documenting.

### 6. `VotingSession.session_id` semantics retargeted, not denormalized

The column type is unchanged (`legislative_sessions.id`, `ondelete=SET NULL`)
but its meaning is now unambiguous: it points at a single Sesión meeting. No
denormalized `legislature_id` / `period_id` columns are added on
`VotingSession`; those are reached via `VotingSession → LegislativeSession →
Legislature → LegislativePeriod`.

Meeting-level ingestion is not yet wired (the `get_sesiones(legislatura_id)`
client method exists at `app/ingestors/clients/camara.py:155` but is not
invoked by `run_ingest_legislature`). Until it is, `VotingSession.session_id`
will be null for new ingests. This is acceptable because the user-facing
"which Legislatura did this vote happen in?" question is answerable through
the dated joins (`VotingSession.voting_date` falls within `Legislature`'s
half-open window).

## Alternatives considered

**Two-tier with derived Legislatura number.** Keep only `LegislativePeriod`
and `LegislativeSession`; add a computed property on `LegislativePeriod` that
yields the current Legislatura number for a given date. Rejected because
Legislatura is never a first-class entity, votes cannot reference it directly,
and the historical sequence (374) is not preserved across schema changes.

**Two-tier "rename only" (no `Legislature` model).** Acknowledge that what
`parse_legislature()` already writes is the Legislatura concept; rename the
existing `LegislativeSession` table to `legislatures` and defer modeling
Sesión until we actually ingest meetings. Rejected because the user's spec
explicitly requires Sesión as a first-class concept (it's where votes happen,
where Sala/Comisión venue is recorded, and where the Tabla agenda will
eventually attach).

**Mixed date-range conventions per tier.** Keep `LegislativePeriod` inclusive
(matching the Spanish phrasing "ends on March 11") while making Legislatura
half-open. Rejected because every range query would need two code paths.

## Out of scope

- **Tabla / agenda modeling** — needs an ingestor we do not have; tracked
  as a follow-up. When added, it will live as a child of `LegislativeSession`.
- **February recess as a stored fact** — observable as absence of upcoming
  Sesiones; no DB column.
- **API exposure of `Legislature`** — the public voting schema currently
  surfaces `session_ref` as a string label. Exposing Legislatura/Período via
  the public endpoint is a separate UX decision.
- **Joint sessions (Congreso Pleno)** — not in the user's spec; not modeled.

## Consequences

- Schema regeneration is required (pre-release flow: `just recreate-db`); no
  Alembic migration.
- The `LegislatorTerm.period_id` FK from [ADR-0015] still resolves correctly
  — periods are unchanged in shape.
- Existing tests pass without fixture changes because no test directly
  constructed `LegislativeSession` and the period fixtures already used the
  half-open `end_date = date(2030, 3, 11)` form.
- New parser unit tests cover `LEGISLATURE_KIND_MAP`, the Legislatura
  vs Sesión vocabulary split, and the `_legislature_number` propagation.
