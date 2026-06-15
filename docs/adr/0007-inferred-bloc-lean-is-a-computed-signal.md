# Inferred bloc lean is a computed behavioral signal, separate from editorial bloc affiliation

**Status:** Superseded by [ADR-0014](0014-bloc-affiliation-and-inferred-lean.md) on 2026-06-15 — consolidated with ADR-0006 into a single bloc-model ADR. Content preserved below for history.

To seed the majority simulator and characterize legislators, we derive each
legislator's *inclinación de voto* — the bloc whose modal vote they matched most
often across the **contested, decisive** voting sessions of the current
legislative period (sessions where oficialismo and oposición took opposite sides
and the legislator voted *for*/*against*) — plus a per-legislator *disciplina
partidaria* rate (how often a party member voted with their own party's modal).
These are **computed, behavioral** signals, precomputed per legislator and
refreshed out-of-band in the mold of `voting_signals` / `VotingWindowAggregate`.

They are deliberately kept **distinct from the editorial bloc affiliation** of
ADR-0006 (`Legislator.default_bloc` + `BlocAffiliation`). The inferred lean never
writes to `default_bloc`, and the simulator's default placement still reads the
editorial value first:

```
default_bloc (editorial) → party.current_bloc (structural) → inferred lean → tray
```

Inferred lean seats an otherwise-unaligned independent **only when it clears a
confidence margin**; below the margin (or below a minimum sample) the seat stays
*sin alinear*. For a party member the lean is **informational only** — it is
surfaced as a seat marker and on the detail page, and never auto-moves them.

## Considered Options

- **Fill `default_bloc` with the inferred value.** Rejected: conflates a
  hand-curated editorial fact with a machine estimate, contradicts ADR-0006's
  "no ingestor", and erases the ability to show a rationale ("8 de 11") or to
  distinguish "we don't know yet" from "editorially unaligned".
- **Compute it only in the frontend.** Rejected: the platform should own this
  (reused on the legislator detail page, and a stepping stone to the deferred
  *consenso atípico* signal), and it would mean shipping vote-level data to the
  browser and recomputing on every load.
- **Recent window (last ~N sessions) instead of the whole period.** Rejected for
  v1: two windows double the stored surface and let the simulator popover
  disagree with the detail page; a weak whole-period lean is a *safe* simulator
  default (leave *sin alinear*) rather than a confident mis-seat. Revisit with
  recency weighting if mid-period realignments prove common.
- **Auto-seat party members on their lean (pre-split low-cohesion parties).**
  Rejected: it makes the *default* scenario reflect past behavior — but the
  simulator is defined as hypothetical — and turns `Defector` (a user move) into
  a load-time state. The cross-pressured-member story is told with a marker and
  on the detail page instead.

## Consequences

- **Bloc-at-date is approximated.** A bloc's position in a past session is the
  modal *for*/*against* among its members at the session date, using party-at-date
  (`LegislatorTerm`, as `compute_quiebre_bloque` already does) mapped through each
  party's *current* bloc — because ADR-0006 exposes only `current_bloc`.
  Acceptable within one period; a true as-of-date bloc map (the *consenso atípico*
  roadmap) would tighten it.
- **New API surface.** A `voting_lean` object on the legislator summary (the
  simulator reads it from the list endpoint) and a `party_discipline` object on
  the detail. Changing their shape later is a contract change, hence this record.
- **Thresholds are documented constants** (minimum contested sessions, seat
  margin) living alongside the `voting_signals` thresholds; tuning them re-seeds
  simulator defaults.
- **`Disciplina partidaria` ≠ `Cohesión de bloque`.** The new per-legislator,
  over-the-period rate must not be confused with the existing per-session,
  per-party cohesion, nor read as the pejorative "indisciplina" the web glossary
  avoids for a one-off *quiebre de bloque*. The web `CONTEXT.md` records the
  distinction.
