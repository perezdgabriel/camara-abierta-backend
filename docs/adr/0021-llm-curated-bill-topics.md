# LLM-curated generic bill topics, folded into the PROPOSAL summary call

**Status:** Accepted, 2026-06-30.

## Context

`Topic` (`app/models/core.py`) had 8,492 rows, all flat despite the model
supporting a `parent_id` hierarchy (`Topic.parent`/`Topic.children`), sourced
from upstream "materias" via a daily `ingest-reference-data` celery-beat job
(`run_ingest_reference_data` → `sync_topic` → `OpenDataCamaraClient.get_materias()`).
These names were narrow legal subjects (`PESCA DEL JUREL`, `MICROEMPRESAS`,
`JUICIOS DE FAMILIA`) — too specific for a generic "areas" UI.

Separately, and more acutely: `bill_topics` (the bill↔topic link table) had
**zero rows** for all 451 bills. ADR-0020 deliberately stopped threading
`materias` through the bill-detail parser when the default ingestor switched
to `restsil` ("a dedicated topic source is recoverable later"). The result:
the dashboard's "Proyectos activos por tema" chart and the legislator page's
"Cómo vota por área" chart both rendered empty states — not because topics
were too granular, but because no bill had any topic at all.

The platform is pre-release (`scripts/recreate_db.py` regenerates the
schema, no delta-sync clients deployed), so redesigning `Topic` carries no
migration cost. ADR-0019 already established a layered, Claude-backed,
structured-output pattern for bill summaries (`BillSummary`, strict
JSON-schema tool-use, `AI_SUMMARY_ENABLED` gate, prompt/model staleness
self-healing) — the natural template to extend rather than invent a new
pipeline shape.

## Decision

### Topic becomes a small curated taxonomy, not a granular import

`Topic` is redefined in place as a flat, generic vocabulary. The unused
`parent_id`/`parent`/`children` hierarchy is dropped from the model. The
granular materias sync is removed entirely: `sync_topic`
(`app/tasks/reference.py`, deleted), `run_ingest_reference_data` /
`ingest_reference_data` (`app/tasks/ingestors.py`), the
`ingest-reference-data` celery-beat schedule entry, the `ingestors
reference-data` CLI subcommand, and `OpenDataCamaraClient.get_materias()`.
Left running, it would keep repopulating `Topic` with narrow legal subjects
and fight the curated vocabulary.

`scripts/seed_topics.py` (mirrors `scripts/seed_blocs.py`) seeds ~18 starting
topics (Trabajo, Salud, Seguridad, Educación, Vivienda, Medio Ambiente,
Pensiones, Tributaria, Género, Justicia, Agricultura y Pesca, Energía,
Relaciones Exteriores, Cultura, Deportes, Tecnología, Municipal, Transporte),
run as part of `just seed`. This anchors the LLM's reuse-preference (below)
to a sensible vocabulary from the first classification run, instead of
letting naming depend on processing order.

### Open vocabulary, with a reuse-preference guardrail

Each classification call includes the current topic list (`db.query(Topic.name)`)
in the prompt and instructs Claude to reuse an existing label whenever it
reasonably fits, only coining a new one when nothing does. New topics go
live immediately on creation — no approval queue — consistent with the
existing upsert-by-slug pattern in `_upsert_topic_record`
(`app/services/write.py`). This was a deliberate trade-off: a closed enum
would guarantee no drift but requires upfront enumeration of every
legislative area Chile's congress might touch; an open vocabulary with no
reuse pressure would just recreate the original "too specific" problem one
bill at a time. The reuse-preference instruction is the middle path.

Each bill gets 1-3 topics, unranked — matching what the UI already renders
(bill cards show up to 4 topic tags) and what the product mockups show (e.g.
"TRABAJO FAMILIA GÉNERO"). The count is enforced by prompt instruction plus a
code-side clamp to the first 3 items in `_generate_proposal_layer`, not by
the JSON schema itself: Claude's strict tool-use mode rejects `minItems`/
`maxItems` on array properties ("not supported"), so `topics` is an
unconstrained `array of string` at the schema level, same as
`affected_groups`/`key_objections`.

### Folded into the PROPOSAL layer — no second Claude call

Topic classification does **not** get its own `BillSummary` kind, task, or
trigger block. Instead, `PROPOSAL_TOOL`'s schema gained a required `topics`
field, and `generate_proposal_summary()` gained an `existing_topics`
parameter threaded into the prompt. `_generate_proposal_layer`
(`app/tasks/bills.py`) queries existing topic names, passes them to the
(now-combined) Claude call, and — on a successful response — calls the new
`apply_bill_topic_classification()` write-service wrapper (a thin layer over
the pre-existing `_reconcile_topics` helper) to write `bill_topics`,
alongside persisting the PROPOSAL `BillSummary` row as before (whose
`content` JSONB now also carries `topics`, giving a free audit trail with no
new storage).

This was chosen over a separate `TOPICS` layer/tool because PROPOSAL already
sends the bill's full text — the expensive part of the input — and the two
layers would have shared identical trigger conditions anyway (`is_new`,
`full_text_url_changed`, `status_changed`, `stage_changed`, plus
prompt/model staleness). A second call would have doubled the full-text
input tokens per bill for no independent lifecycle benefit. The trade-off:
topic freshness is now coupled to PROPOSAL's status — a bill with no
`full_text_url` gets neither a proposal summary nor topics, and there's no
independent `SKIPPED`/`FAILED` row for topics specifically. Given the two
were always going to regenerate together, this coupling is honest rather
than artificial.

### Reuses `AI_SUMMARY_ENABLED`

No separate feature flag. Topic classification is inseparable from the
PROPOSAL call at the code level now, so a second gate would be redundant —
flipping `AI_SUMMARY_ENABLED` controls both.

### Out of scope

No `GET /topics` endpoint and no bills-list topic-filter UI in this pass.
The bill-list `tema_id` filter (`app/api/v1/proyectos.py`) and the
frontend's matching `BillFilters.tema_id` field already exist end-to-end but
have no UI control; that remains dead for now. The frontend
(`camara-abierta-web`) needed **no changes** — `Topic{id, name, slug, icon}`
is already rendered generically everywhere (dashboard distribution chart,
legislator voting-by-area chart, bill card/detail tag badges), and the
legislator topic-affinity query (`get_legislator_topic_affinity`,
`app/services/legislators.py`) already joins through `bill_topics`/`Topic`
generically with no hardcoded list.

## Consequences

**Positive**:
- Both previously-empty UI widgets populate automatically once `bill_topics`
  has rows — no frontend work.
- One Claude call per bill instead of two; input-token cost for topics is
  effectively free.
- The curated taxonomy can't be silently re-polluted by a forgotten
  background sync, because that sync no longer exists.
- Topic content lives inside the existing PROPOSAL `BillSummary` row —
  staleness, retries, and backfill (`ai bills regenerate --kind proposal`)
  all work unchanged.

**Negative**:
- Topics can't be regenerated independently of the proposal summary (e.g. to
  pick up a taxonomy-only prompt change without re-running the citizen
  summary). Given they share a prompt version and trigger conditions today,
  this is not expected to matter in practice.
- Bills without `full_text_url` get no topics, same as they get no proposal
  summary — there is no lower-bar fallback (e.g. title-only classification).
- Open vocabulary means the topic list can still grow over time if the
  reuse-preference instruction under-performs; this needs periodic review
  via the admin panel, same as any editorial vocabulary.

## Alternatives considered

- **Separate `TOPICS` `BillSummaryKind` + dedicated Claude call.** Cleaner
  separation of concerns and independent observability (its own
  `SKIPPED`/`FAILED` rows), but doubles full-text input tokens per bill for
  a layer whose trigger conditions are identical to PROPOSAL's. Rejected for
  cost with no compensating lifecycle benefit.
- **Closed/fixed topic enum.** No drift risk, but requires enumerating every
  legislative area upfront and a schema change to add one later. Rejected in
  favor of open vocabulary + reuse-preference, which adapts without code
  changes.
- **Keep the granular materias table for other future uses, add a separate
  curated `Area` concept alongside it.** Considered, but `bill_topics` was
  already empty and nothing else in the codebase reads `Topic` for a
  granular-legal-subject use case — keeping two taxonomies alive would mean
  maintaining a sync path for one of them that nothing currently consumes.
  Rejected; can be reintroduced later if a real consumer appears.
