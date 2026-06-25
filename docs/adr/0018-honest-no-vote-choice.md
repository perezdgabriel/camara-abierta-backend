# Honest `NO_VOTE` choice with Senate-side synthesis

**Status:** Accepted, 2026-06-25.

## Context

`VoteChoice.ABSENT` was the catch-all bucket for any legislator who did not
cast a classified vote in a session. Two problems made it dishonest and
incomplete:

1. **It made a factual claim we can't substantiate.** No upstream feed tells
   us *why* a legislator did not vote — sick, on official mission, on
   approved *permiso constitucional*, or skipping. Naming the row "absent"
   asserts a fact we don't have. A legislator at home recovering from
   surgery showed up in the platform as absent on every session that week.
2. **It silently dropped real upstream signal.** OpenData Cámara already
   exposes `<TipoOpcionVoto Valor="4">No Vota</TipoOpcionVoto>` alongside
   `Valor="3" Dispensado`, but the chamber parser only switched on values
   `0`/`1`/`2` and bucketed `Valor="4"` into the fallback (`ABSENT`). The
   distinction the upstream sends was being thrown away.
3. **Senate side never materialised non-voters at all.** The restsil
   `buscarVotaciones` feed (ADR-0013) emits per-senator entries only for
   the SI / NO / ABSTENCION / PAREO buckets. Senators who did not vote
   produced no row, so per-legislator stats and any "share who didn't
   vote" signal undercounted Senate sessions.

The platform is pre-release; `recreate_db.py` regenerates the schema and no
delta-sync clients are deployed yet, so the rename can be wholesale with no
backfill, migration script, or compatibility shim.

## Decision

### 1. Rename `VoteChoice.ABSENT` → `VoteChoice.NO_VOTE` (`"no_vote"`)

A straight rename. Spanish UI label *"No vota"* (present tense, matches the
phrasing the Cámara de Diputados itself uses). The value covers the entire
old `ABSENT` meaning: "the legislator left no classified vote on this
session, and we don't know why."

No dormant value is reserved. If we later ship an excused-absence ingest
(e.g. *permisos constitucionales*) it will introduce a new dedicated
enum value at that time rather than reuse a name that pre-existed without
the data to back it.

### 2. Chamber parser maps every upstream code, defaults to `NO_VOTE`

`_parse_chamber_vote_choice` (`app/ingestors/parsers/votes.py`) now switches
on the full upstream code table:

| `OpcionVoto/@Valor` | Label        | `VoteChoice` |
|---|---|---|
| 0 | En Contra    | `AGAINST` |
| 1 | Afirmativo   | `FOR` |
| 2 | Abstención   | `ABSTAIN` |
| 3 | Dispensado   | `DISPENSED` |
| 4 | No Vota      | `NO_VOTE` |

Fallback (unknown label and unknown code) is `NO_VOTE`. Label map gains
`"no vota"` for parity.

### 3. Senate `NO_VOTE` synthesis happens in the write service

`_reconcile_votes` (`app/services/write.py`), when the session's chamber
is `ChamberType.SENATE`, queries every `LegislatorTerm` whose
`[start_date, end_date)` covers `voting_session.voting_date` and whose
`chamber_id` is the Senate. For each `chamber_external_id` not already in
the upstream payload, the reconciler appends a synthetic
`Vote(vote=NO_VOTE, legislator_id=<term.legislator_id>,
legislator_external_id=<term.chamber_external_id>)`.

Senators whose terms have not been ingested yet are silently skipped — the
orphan-safe stance ADR-0015 takes for unresolved bridges; the next refresh
after term ingestion claims them. Chamber-side sessions are *not*
synthesised: upstream already sends a per-deputy row for `No Vota`.

### 4. `VotingSession.no_votes` is derived, not ingested

Chamber XML carries no `TotalNoVota` aggregate and the restsil Senate feed
exposes no aggregate either, so the column is computed from the reconciled
individual votes after synthesis (`sum(1 for p in desired.values() if
p["vote"] == VoteChoice.NO_VOTE)`) and assigned to the session before
flush. The old `parse_chamber_vote` `"absences"` field is removed from the
parser output.

### 5. Companion renames for the honest framing

| Old name | New name | Where |
|---|---|---|
| `VotingSession.absences` | `VotingSession.no_votes` | `app/models/votacion.py` |
| `LegislatorVotingStats.absences` | `LegislatorVotingStats.no_votes` | `app/models/votacion.py` |
| `LegislatorVotingStats.attendance_percentage` | `LegislatorVotingStats.record_rate` | `app/models/votacion.py` |
| `SignalType.ALTO_AUSENTISMO` | `SignalType.BAJO_REGISTRO` | `app/models/enums.py` |
| `compute_alto_ausentismo()` | `compute_bajo_registro()` | `app/services/voting_signals.py` |
| `ABSENCE_RATE_MIN`, `ABSENCE_BASELINE_WINDOW_DAYS` | `NO_VOTE_RATE_MIN`, `NO_VOTE_BASELINE_WINDOW_DAYS` | same |
| `avg_attendance()` (aggregate) | `avg_participation()` | same |
| `VotingAggregates.avg_attendance` (schema) | `VotingAggregates.avg_participation` | `app/schemas/voting.py` |
| Signal payload keys `absences` / `baseline_absences` / `absence_rate` | `no_votes` / `baseline_no_votes` / `no_vote_rate` | same |

`record_rate` keeps the prior formula `(total − no_votes) / total * 100` —
the share of sessions where the legislator left *any* recorded entry. The
*name change* is the point: calling it "attendance" implied physical
presence we cannot observe.

`participation_rate` is unchanged — it has always meant
`(for + against + abstain) / total`, the share of decisive opinions.

## Alternatives considered

- **Keep `ABSENT` dormant for a future excuse feed.** Rejected. Carrying a
  name we don't emit invites confusion about what the row means and tempts
  future code to read it. When we ship the excuse feed we'll add a new
  value (e.g. `EXCUSED`) whose meaning is grounded in real data.
- **Leave Senate sessions sparse.** Rejected. The whole point of the
  rename is to enable honest "share who did not vote" computations; leaving
  the Senate side empty would silently undercount `record_rate` and
  `BAJO_REGISTRO` for half the platform.
- **Compute the Senate synthesis lazily inside the stats refresh.**
  Rejected. A read-time computation would diverge from the materialised
  `VotingSession.no_votes` column and complicate the per-vote audit trail.
  Materialising at write time keeps the data model self-consistent.
- **Rename `attendance_percentage` to `presence_rate`.** Rejected for the
  same reason as `ABSENT`: "presence" is a claim about physical attendance
  we don't have. `record_rate` is descriptive of what the metric measures
  ("rate of leaving a record").

## Consequences

- **Sync clients (web / mobile) must update.** The delta-sync protocol
  ships vote rows verbatim; consumers will receive `"no_vote"` enum
  strings, the renamed `bajo_registro` signal type, and the renamed
  schema fields (`no_votes`, `record_rate`, `avg_participation`).
  Pre-release: no live clients to coordinate against, but the frontend
  repos need a matching update before any user-facing surface is built.
- **Schema regen via `just recreate-db`.** No Alembic migration. Existing
  dev databases with `'absent'` values get wiped along with the rest.
- **Stats column meaning preserved.** `record_rate` and the
  `BAJO_REGISTRO` thresholds keep their numeric definitions; only the
  labels change. No threshold tuning required.
- **`alto_ausentismo` rows in the signals table become orphan after
  recreate-db** (they live in JSON, but the parent enum value is gone).
  Since the table is rebuilt on every signal recompute by the beat task,
  this resolves on the next tick.
