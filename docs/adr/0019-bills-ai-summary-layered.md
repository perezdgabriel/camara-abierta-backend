# Layered bill AI summary with versioning and event-driven regeneration

**Status:** Accepted, 2026-06-29.

## Context

The first cut of the bills AI summary feature was minimal: one `Bill.ai_summary`
Text column, regenerated on **every** `sync_bill` call, using Gemini (with an
OpenWebUI/Ollama fallback) against the first 12 000 characters of the PDF
behind `Bill.full_text_url`. The prompt was hardcoded and the model emitted
free-text Spanish prose. None of the generation context (prompt version,
model name, source hash) was persisted.

Four problems surfaced once the feature started running at scale:

1. **Cost from wasted calls.** The Celery task ran unconditionally on every
   bill upsert, even when neither the PDF nor the bill's metadata had
   changed. The DB write was idempotent on the summary string, but the PDF
   download and the LLM call always happened.
2. **Quality ceiling.** The 12 KB truncation silently chopped large bills,
   the single prompt did not distinguish proposal-time content from
   downstream comparados, and free-text output gave the UI no structured
   fields to render lists/cards.
3. **Coverage opacity.** Failures and "no PDF" cases produced no row at
   all — callers couldn't distinguish never-tried from tried-and-skipped.
   PDF extraction errors logged a warning and returned silently.
4. **Lifecycle blindness.** Upgrading the prompt or the model required a
   manual full-corpus regeneration; nothing on the row recorded which
   prompt/model had produced the current summary, so there was no automatic
   way to detect or backfill staleness.

The platform is pre-release. `scripts/recreate_db.py` regenerates the schema
and no delta-sync clients are deployed, so a structural change to the AI
summary storage is feasible with no migration or compatibility shim.

## Decision

Rebuild the feature around **three independent summary layers**, a dedicated
table, Anthropic Claude as the sole provider for bill summaries, and
event-driven regeneration with self-healing on prompt/model upgrades.

### Layers

- **PROPOSAL** — LLM-generated. Input: PDF text from `Bill.full_text_url`.
  Structured output `{propose, affected_groups, why_it_matters, key_objections}`
  via Claude tool-use.
- **AMENDMENTS** — LLM-generated. Input: concatenated text of every
  `BillDocument` whose `document_type == "comparison"`. Structured output
  `{changes: list[str]}`.
- **STATUS_LINE** — deterministic, derived in the API from
  `Bill.status` + current `BillStage.stage_type` + `current_committee` +
  `last_activity_date`. No LLM, no storage. Always populated so the bill
  detail page is never empty.

### Storage

A new `bill_summaries` table, one row per `(bill_id, kind)` enforced by a
unique constraint. Each row carries:

- `kind` (`BillSummaryKind` ∈ `proposal | amendments`),
- `status` (`BillSummaryStatus` ∈ `success | skipped | failed`),
- `content` (JSONB; layer-specific structured fields),
- `prompt_version`, `model_name`,
- `source_url`, `source_url_hash` (SHA-256, cheap "changed?" gate),
- `error_reason` (free text, for `SKIPPED`/`FAILED` rows),
- `generated_at`,
- inherits `SyncableMixin` so mobile clients pick up updates.

`Bill.ai_summary` and `Bill.ai_summary_updated_at` are removed.

### Provider

Anthropic Claude (`claude-haiku-4-5` default) exclusively for bills,
no fallback chain. Structured output is enforced via Claude tool-use against
JSON Schemas defined in `app/services/llm.py`. Failures persist as
`status=FAILED` rows with the exception message in `error_reason`.

The Gemini + OpenWebUI clients remain in `llm.py` for the **norms**
(Diario Oficial) pipeline, which is out of scope here.

### Triggers

`sync_bill` no longer fires generation unconditionally. `upsert_bill` returns
a `change_info` dict extended with `full_text_url_changed` and
`new_comparado_added`. A decision helper in `app/tasks/bills.py` translates
those into the set of layer kinds to enqueue. A layer is regenerated iff any of:

- no `bill_summaries` row exists for `(bill_id, kind)`;
- the row's `prompt_version` or `model_name` differs from current settings
  (self-healing on upgrades);
- kind-specific signal fired:
  - **PROPOSAL:** `is_new`, `full_text_url_changed`, `status_changed`, or
    `stage_changed`.
  - **AMENDMENTS:** `new_comparado_added`.

### Operations

- The entire feature is gated by `AI_SUMMARY_ENABLED` (default `false`). The
  gate is checked at both enqueue sites — `sync_bill`'s trigger decision and
  the `ai bills regenerate` backfill — so a fresh `ingestors bills` scan
  against an empty DB does not silently burn LLM budget, and a stale
  `--stale-only` invocation against the full corpus respects the same
  switch. Flip on per environment when ready to pay.
- `python -m app.cli ai bills regenerate [--bulletin X] [--kind …] [--stale-only]`
  enqueues regeneration tasks idempotently. With `--stale-only`, only rows
  whose stored `prompt_version` or `model_name` differs from current
  settings are enqueued.
- Failures (no PDF, extraction failure, LLM error) are persisted as
  explicit `SKIPPED` / `FAILED` rows with `error_reason`. The API surfaces
  `proposal=null` (or `amendments=null`) when the layer has no successful
  content — never a misleading metadata-only blurb. The `status_line` layer
  guarantees the bill detail is never empty.

## Consequences

**Positive**:
- LLM calls are bounded by event signals + version mismatches, not poll
  rate. Steady-state cost falls to "only when something changed."
- Structured output unlocks richer UI (lists of affected groups, objections,
  amendment deltas) without a free-text parse step.
- Prompt/model upgrades become a config bump: the next sync of each bill
  detects the mismatch and re-runs that one layer; `--stale-only` cleans
  dormant bills.
- Coverage is observable: every attempt leaves a row; `SKIPPED` and
  `FAILED` rows distinguish "tried and gave up" from "never tried."

**Negative**:
- Two LLM calls per heavily-amended bill instead of one. The PROPOSAL +
  AMENDMENTS split is deliberate (different lifecycles, different sources),
  but it does multiply per-bill cost for the subset of bills that
  accumulate comparados.
- The structured tool-use schema is more brittle than free text: if Claude
  refuses to emit the tool call, the layer goes to `FAILED`. We accept this
  in exchange for predictable output shape.
- The status_line text is composed in Spanish in the API layer (no i18n).
  The platform is Chile-only, so this is acceptable for v1.

## Alternatives considered

- **Keep one Text column, fix only the trigger.** Cheapest, but leaves the
  quality, structure, and lifecycle problems unsolved.
- **Single JSONB column on `Bill`.** Flexible, but loses status-per-layer
  observability and makes versioning a per-key dance inside the blob.
- **Stay on Gemini, add prompt versioning.** Possible, but Gemini's tool
  use is less consistent for Spanish output in our testing, and the
  OpenWebUI fallback has a different output contract — keeping both
  doubled the prompt surface. Standardising on one provider was cheaper.
- **Metadata-only summary fallback when no PDF.** Tried in the design
  pass; rejected. A summary from title + topics + ministries reads
  confidently but says nothing the title doesn't. We persist a `SKIPPED`
  row with `error_reason="no_full_text_url"` and let the API render
  `proposal=null`.
