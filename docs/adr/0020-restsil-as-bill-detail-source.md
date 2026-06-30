# Restsil as the bill-detail source

**Status:** Accepted, 2026-06-29. Supersedes the wspublico-detail decision
embedded in [ADR-0013](0013-bill-and-vote-ingest-pipeline.md) ("Bill detail
— wspublico `tramitacion.php`").

## Context

ADR-0013 split the bills pipeline so that restsil owns discovery
(`buscarProyectosDeLey`) and wspublico owns detail
(`tramitacion.php?boletin=X`). The detail payload carries the foundational
PDF URL (`<link_mensaje_mocion>` → `Bill.full_text_url`) and the per-trámite
informes/comparados/oficios links (`BillDocument.document_url`).

The wspublico endpoint still serves XML reliably, but its document URLs
have stopped resolving. Two downstream failures forced the swap:

1. The frontend's "ver documento" links 404 across the corpus.
2. The PROPOSAL layer of the bill AI summary (ADR-0019) calls
   `extract_text_from_url(Bill.full_text_url)` on the wspublico-shaped URL
   and silently `status=FAILED` on every bill — every PROPOSAL row was
   landing as a failure with no underlying PDF to summarise.

Restsil already exposes a per-bill detail endpoint at
`proyectos/tramitacionProyecto/{proy_id}`. Its document links point at a
sibling service, `microservicio-documentos.senado.cl/v1/archivos/{uuid}`,
which serves `application/pdf` directly with no apikey header.

## Decision

`run_ingest_bills` calls restsil for bill detail by default. The
`tramitacionProyecto/{proy_id}` payload has three sections that
`BillParser.parse_restsil_detail` maps onto the same DB-shape contract
`parse_bill` produces, so `upsert_bill` and all `_reconcile_*` writers are
untouched.

- `infoProyecto` — metadata (suma, iniciativa, origen, urgencia, estado-as-
  etapa, subetapa, leynro, fecha de publicación).
- `etapasProyecto` — one stage per upstream etapa; first non-null
  `link_mensaje` populates `Bill.full_text_url`.
- `tramitacionProyecto` — one `BillEvent` per row; per-row
  `LINK_INFORME / LINK_COMPARADO / LINK_OFICIO` populate `BillDocument`.

Authors for mociones come from the `AUTORES` field on the discovery row
(slash-separated canonical-name form), threaded through
`_discover_bulletins_restsil` → `_fetch_bill_details_restsil` →
`parse_restsil_detail`. The canonical-key matcher in
`_reconcile_authorships` already normalises this format unchanged.

A new `INGESTOR_BILL_DETAIL_SOURCE: Literal["restsil","wspublico"]` flag
(default `"restsil"`) gates the source. It is independent of
`INGESTOR_BILLS_SOURCE`. When discovery is `opendata` (failover) but detail
is `restsil`, `_fetch_bill_details_restsil` does a per-bulletin
`search_bills(boletin=X)` call to resolve `PROYID`.

Restsil's `infoProyecto.ResumenIA` (an upstream-baked free-text HTML
summary) is **ignored**. ADR-0019 chose structured Claude tool-use for the
PROPOSAL layer; mixing free-text prose into the same row would defeat the
schema contract.

## Trade-offs accepted

- `BillUrgency` history rows stop growing. `Bill.current_urgency` still
  flows from `infoProyecto.Urgencia`. Per-event urgencia rows in the
  upstream `tramitacionProyecto` log are free text (`"hace presente la
  urgencia Suma"`) and a regex-parser is recoverable later.
- Topic (`materias`) ingest stops on the restsil-detail path. `MATERIAS`
  is sparse / null for recent bills upstream anyway. A dedicated topic
  source is recoverable later.
- The embedded `<votacion>` block from wspublico is dropped — already
  irrelevant under ADR-0013 (Senate / chamber votes are owned by dedicated
  tasks).
- Two HTTP calls per `--bulletin` invocation when discovery is bypassed
  (`search_bills` + `tramitacionProyecto`). Steady-state full scan keeps the
  one-call-per-bulletin shape.

## Failover

`INGESTOR_BILL_DETAIL_SOURCE=wspublico` re-activates the legacy path:
`fetch_bills_parallel` → `SenadoClient._parse_bill_xml` → `parse_bill`.
The legacy code paths stay compilable and tested. No automatic source
switching — same reasoning as ADR-0013 (silent dedup-key shape change
mid-stream is a worse failure mode than transient errors absorbed by
tenacity retries).

## Migration

Pre-release; standard `scripts/recreate_db.py` + reseed playbook applies.
Initial reseed runs with `AI_SUMMARY_ENABLED=false`; flip on after
document URLs are verified end-to-end. No migration script.

## Consequences

- Documents start resolving on the frontend and in the AI PROPOSAL layer.
  `Bill.full_text_url` and `BillDocument.document_url` rows take the
  `microservicio-documentos.senado.cl/v1/archivos/{uuid}` shape.
- `_decide_summary_triggers` will fire `full_text_url_changed=true` on the
  first restsil ingest after cutover, regenerating every PROPOSAL summary
  — but the recreate_db reseed makes every bill `is_new=True` first, so
  the trigger fires once cleanly rather than as a delta storm.
- One extra restsil call per bulletin in the cross-source combination
  (opendata discovery + restsil detail) for the `PROYID` lookup. The rare
  path; acceptable cost.
- New env var `INGESTOR_BILL_DETAIL_SOURCE` (default `"restsil"`).
