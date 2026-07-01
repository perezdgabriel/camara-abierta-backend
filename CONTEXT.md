# Cámara Abierta

A broad legislative transparency platform for Chile. It tracks bills, legislators, voting sessions, and committees through the Chilean Congress, and also monitors the Diario Oficial and CGR regulations.

## Language

**Cámara Abierta**:
The platform itself. Not "Diario Oficial API" (that's a legacy label in the settings).
_Avoid_: Diario Oficial API

**Domain language**:
All model class names, table names, and internal identifiers use English. Spanish is used only in user-facing content (API responses, UI text). Legacy Spanish names in the codebase (e.g. `NormaGeneral`, `Reglamento`) are candidates for renaming.

**Enums**:
All categorical string fields use formal Python enums. Current vocabulary:
- `BillStatus`: pending, approved, rejected, archived, withdrawn, unconstitutional, enacted, published
- `BillOrigin`: executive, deputies
- `BillType`: project (default)
- `StageType`: first_constitutional_tramite, second_constitutional_tramite, third_constitutional_tramite, mixed_commission, constitutional_tribunal, promulgation, publication
- `UrgencyType`: simple, sum, immediate
- `VotingType`: general, particular, single, other
- `VoteChoice`: for, against, abstain, paired, dispensed, no_vote
- `VotingResult`: approved, rejected, tie
- `ChamberType`: deputies, senate
- `CommitteeType`: permanent, special, investigative, mixed
- `LegislatureKind`: ordinaria, extraordinaria (annual Legislatura — post-2005 always `ordinaria`)
- `SessionKind`: ordinaria, especial (single Sesión Legislativa — distinct from `LegislatureKind`)
- `CalendarEventKind`: sesion, votacion, comision, interpelacion, mensaje, plazo, acusacion_constitucional, informe_cei, otro
- `CalendarEventSource`: manual, tabla_semanal (grows as upstream agenda scrapers ship)

**Sponsoring ministries**:
Optional zero-to-many ministries attached to a bill as upstream metadata from the Chamber of Deputies. They are historical bill-scoped labels, not a shared platform-wide ministry catalog.
_Avoid_: ministries, ministry catalog

**Dispensed vote**:
An excused legislator vote choice recorded explicitly by an upstream chamber voting source. It is distinct from a *no vota* (the legislator left no recorded vote at all, with no upstream excuse marker).
_Avoid_: absent

**No vota**:
`VoteChoice.NO_VOTE` (Spanish UI label *"No vota"*) means the legislator left no classified vote on the session. Chamber side comes from upstream `<TipoOpcionVoto Valor="4">No Vota</TipoOpcionVoto>` (and is the parser's fallback for unrecognised values); Senate side is **synthesised** server-side inside `_reconcile_votes` (`app/services/write.py`) for every senator whose `LegislatorTerm` covers the session's `voting_date` but who is absent from every upstream restsil bucket — restsil emits only per-bucket presence, so synthesis is the only place senate non-voters get materialised. Senators with no covering term are silently skipped (orphan-safe, mirrors ADR-0015). The aggregate `VotingSession.no_votes` is derived from the reconciled list, not from any upstream summary (chamber XML has no `TotalNoVota`, restsil no aggregate). Powers the `record_rate` metric (`LegislatorVotingStats.record_rate = (total − no_votes) / total * 100`, the share of sessions where the legislator left *any* recorded entry) and the `BAJO_REGISTRO` signal (`SignalType.BAJO_REGISTRO`, fires when `no_votes/total_seats` exceeds `NO_VOTE_RATE_MIN`). Why not "absent": no upstream signal tells us *why* the legislator did not vote — sick, on official mission, excused, or skipping — so naming the row "absent" is a factual claim we cannot substantiate. A future excused-absence ingest (e.g. *permisos constitucionales*) will add a new dedicated enum value rather than reusing this one. See ADR-0018.
_Avoid_: "absent", "ausentismo", `VoteChoice.ABSENT` (removed); `attendance_percentage` (renamed to `record_rate`); `ALTO_AUSENTISMO` signal (renamed to `BAJO_REGISTRO`)

**Bill lifecycle**:
`Bill.status` is the source of truth from the upstream Congress API, not derived from stages. `BillStage` records are the detailed legislative history. The `is_current` flag on `BillStage` tracks where the bill currently is in the process.

**Bill activity**:
`BillEvent` is the granular legislative activity log for a bill, derived from upstream `tramitaciones`. `BillStage` remains the coarser progression model used for current-stage tracking. `last_activity_date` means the latest `BillEvent.event_date`, falling back to `Bill.entry_date` when a bill has no events.

**Moción** (bill origin):
A bill initiated by parliamentarians — modeled as `Bill.origin == BillOrigin.DEPUTIES`. The counterpart is a **Mensaje**, initiated by the Executive (`BillOrigin.EXECUTIVE`). Upstream `iniciativa` text — `Mocion`, `Moción`, `Indicacion`, `Indicación` — all map to `DEPUTIES` via `ORIGIN_MAP` in `app/ingestors/parsers/bills.py`. Mociones carry per-author identity via `BillAuthorship` rows; mensajes typically have no individual authors. Chilean rules cap signers at 10 deputies or 5 senators per moción — see `app/services/audit/mocion_authors.py` for the audit that surfaces violations and matching failures.
_Avoid_: "moción" without context (web `CONTEXT.md` "Documento" entry uses _moción_ in the foundational-document sense — the message-or-motion text excluded from the documents list; that's the same word, different concept).

**Bill authorship**:
`BillAuthorship` joins `Bill` ↔ `Legislator` with an `author_type` (`"author"` for mociones; `"executive"` reserved for the rare mensaje-with-author case). Ingestion in `_reconcile_authorships` (`app/services/write.py`) matches the upstream `<autor><PARLAMENTARIO>` text against `Legislator.full_name` via a **canonical-key match** (case-, accent-, order-, whitespace-, and punctuation-insensitive) — `_canonicalize_legislator_name` folds the upstream `"Apellido_paterno Apellido_materno, Nombres"` format into the DB `"Nombres Apellido_paterno Apellido_materno"` form, then strips accents and collapses non-alphanumeric runs. Unmatched names emit a `WARNING` log with the bulletin and raw name so the silent-drop regression class is visible without an audit run; key collisions emit `ERROR` and both colliding legislators drop out of the lookup. `audit mocion-authors` is the periodic regression check, not the primary safety net.
_Avoid_: treating zero-author mociones as semantically valid; reintroducing a per-name `SELECT` in the matcher (a per-call canonical-key dict is cheaper and more robust); reading "exact name match" docs that predate the canonical-key rewrite

**Data collector**:
A component that fetches external data and writes it to the database. Current implementations: `scrapers/` (browser-driven via Playwright) and `ingestors/` (API clients via httpx). The split is an implementation detail — both produce structured data for the write service.
_Avoid_: scraper, ingestor (when referring to the concept; fine as directory names)

**Sync**:
A client-side delta sync protocol. Mobile/SPA clients poll for entity changes since their last known `sync_version`. `ClientSyncState` tracks per-device progress. `ChangeLog` records mutations. `SyncableMixin` stamps each entity with a global version sequence.
_Avoid_: delta sync, incremental sync (prefer just "sync")

**Subdomains**:
Three independent data domains share the platform:
1. **Legislative** — Bills, Legislators, Voting, Committees, Political Parties
2. **Diario Oficial** — Normas Generales (daily government gazette)
3. **CGR Reglamentos** — Regulatory decrees from the Contraloría General
No cross-domain references exist between them.

**AI enrichment**:
A core feature. Legislative language and official gazette content are simplified for the general public. *Bills* carry layered structured summaries in a dedicated `bill_summaries` table — one row per `(bill_id, kind)` where `kind ∈ {proposal, amendments}`. The proposal layer summarises the mensaje/moción PDF; the amendments layer summarises accumulated comparados. A deterministic `status_line` ("En primer trámite constitucional, Comisión de…") is composed in the API at read time, never stored. Each row carries `prompt_version`, `model_name`, `source_url`, `source_url_hash`, `status` (`success|skipped|failed`), and `error_reason` — so callers can distinguish never-tried from tried-and-skipped/failed, and prompt/model upgrades self-heal on next sync. The amendments layer also carries `truncated` — set when the concatenated comparado texts exceeded the input budget and had to be cut before reaching the LLM, meaning the generated `changes` list may not reflect every accumulated change; exposed to API callers so they can flag the layer as possibly incomplete. *Bills* use Anthropic Claude (`claude-haiku-4-5`) exclusively (no fallback chain — failures persist as `status=FAILED` rows). *Normas* still use the Gemini/OpenWebUI stack for executive summaries, key points, beneficiaries, and citizen importance ratings. Regeneration triggers for bills: first sight, `full_text_url` change, `status` change, current stage change, new comparado document, or stored `prompt_version`/`model_name` mismatch. The whole bill-summary path is gated by `AI_SUMMARY_ENABLED` (default `false`) — applies uniformly to the auto-enqueue in `sync_bill` and the `ai bills regenerate` CLI; flip on per environment when ready to pay. See ADR-0019.
_Avoid_: a single `Bill.ai_summary` Text column (removed); free-text bill summaries without prompt/model versioning; running the LLM call on every `sync_bill` regardless of what changed; treating a missing summary row as identical to a tried-and-skipped row

**Geography**:
Chile's administrative and electoral geography: `Region`, `Province`, `Commune` (administrative), `District` (Chamber of Deputies), `Circumscription` (Senate). Users find their representatives by selecting their commune, which maps to both a district and a circumscription. The authoritative current baseline is the checked-in dataset at `app/geography/data/chile_current.json`, loaded manually with `python -m app.cli geography` and applied atomically via `apply_geography_dataset`. Geography is no longer fetched from OpenData at all — the OpenData `reference-data` ingest it used to share with topics was removed once topics became LLM-curated (see Topics, below; ADR-0021). `IngestorState(entity_type="geography")` stores the applied dataset version in `last_cursor`.

**Search**:
Elasticsearch indexes bills for full-text search across titles, summaries, and full text. Other domains (normas, reglamentos) use filtered DB queries.

**Notifications**:
A core feature (not yet implemented). Users will subscribe to bills, representatives, topics, etc. and receive updates. Currently only internal email alerts exist via Resend.

**Admin panel**:
Internal tool (`sqladmin`) for manual data management. Used to update reference data or fix records when data collectors can't capture them.

**Topics**:
A small, flat, curated set of generic legislative-area tags (e.g. Trabajo, Salud, Educación) — not upstream legal "materias", and no longer hierarchical. Claude assigns 1-3 per bill as part of the proposal layer's call (no separate LLM call), preferring to reuse an existing topic over coining a new one; new ones go live immediately, no approval step. Seeded with a starting vocabulary (`scripts/seed_topics.py`) so early classifications converge instead of drifting. Also editable via the admin panel. See ADR-0021.
_Avoid_: treating `Topic` as hierarchical (the `parent_id` field was dropped); assuming topics come from an upstream API ingest (that path was removed)

**Período Legislativo**:
The 4-year presidential and Chamber-of-Deputies cycle — all 155 deputies are elected together, anchored to the presidential mandate. Stored as `LegislativePeriod`. Date range is half-open `[start_date, end_date)`; the 2026–2030 period is `[2026-03-11, 2030-03-11)`. `number` is the historical period count. Senators serve 8-year terms with half renewing each period; `LegislatorTerm.period_id` ties each stint to its covering period. See ADR-0016.
_Avoid_: treating period boundaries as inclusive; calling the 1-year cycle a "period"

**Legislatura**:
The 1-year working cycle of Congress. Post-2005 reform — a `Legislatura Ordinaria` runs continuously from March 11 of one year to March 10 of the following, broken only by the traditional February receso legislativo. Stored as `Legislature`, with `number` the **historical sequential count** dating to the 19th century (e.g. `Legislatura 374` = 2026–2027), populated from upstream — never synthesized. Date range is half-open `[Mar 11 year N, Mar 11 year N+1)`. `Legislature.period_id` ties each Legislatura to its covering Período. `kind` is `LegislatureKind {ordinaria, extraordinaria}` for historical fidelity; new rows are `ordinaria`. The recess is not a stored fact — it's observable as the absence of Sesiones in February. See ADR-0016.
_Avoid_: storing the annual cycle in `LegislativeSession`; calling it a "session"; synthesizing the historical number when upstream omits it

**Sesión Legislativa**:
A single scheduled meeting (typically Tue–Thu) where parliamentarians gather in the Sala (Chamber floor) or in a Comisión (Committee) to debate, vote on bills, or exercise oversight. Stored as `LegislativeSession` — the model represents *the meeting*, not the annual cycle. `legislature_id` ties each meeting to its covering Legislatura. `kind` is `SessionKind {ordinaria, especial}` — ordinarias are the regular mandated meetings on predetermined days; especiales are convened outside regular hours (e.g. minister interpellations or urgent national issues). Venue is encoded by `committee_id`: null = Sala (plenary); non-null = Comisión meeting of that committee. `chamber_id` is retained (committees themselves belong to one chamber). The daily agenda (`Tabla`) is not yet modeled. See ADR-0016.
_Avoid_: using `ordinary`/`extraordinary` as session subtype labels (that's the *Legislatura* vocabulary, not the Sesión vocabulary); reading `LegislativeSession` rows as annual cycles; assuming every Sesión is plenary

**Calendar event**:
A forward-looking, curator-selected moment in legislative life — a noteworthy Sesión, Comisión hearing, interpelación, presidential mensaje, procedural plazo, constitutional accusation, CEI report reading, or any other item worth highlighting. Stored as `CalendarEvent`. Distinct from `LegislativeSession` (the exhaustive scraper-fed record of *every* meeting, not yet ingested — see ADR-0016) and from `BillEvent` (the granular past-tense activity log per bill). v1 carries two write paths: manual entry through the admin panel, and the `tabla-semanal` CLI (`python -m app.cli ingestors tabla-semanal --pdf <path>`) that parses the Cámara de Diputados weekly agenda PDF and writes through `upsert_calendar_event`. The two paths converge on the same rows, distinguished by `source` (`CalendarEventSource`, currently `{manual, tabla_semanal}` and grows) and deduped by `(source, external_ref)`. Tabla Semanal `external_ref` shape: `tabla-semanal:{boletin}:{YYYY-MM-DD}` (per-bill rows), `tabla-semanal:sesion:{YYYY-MM-DD}` (Sesión headers), `tabla-semanal:acusacion-{slug}:{YYYY-MM-DD}`, `tabla-semanal:cei-{N}:{YYYY-MM-DD}`. Each event has a `kind` from `CalendarEventKind {sesion, votacion, comision, interpelacion, mensaje, plazo, acusacion_constitucional, informe_cei, otro}` and may optionally link to a single `Bill`, `Legislator`, and `Committee`. `sesion` and `votacion` are deliberately distinct: a Sesión is the meeting block ("Sala Diputados — 13:00"), a Votación is a discrete announced vote ("Vota Boletín 100-06 — 14:30") — clients agendize votaciones as their own row even when they sit inside a Sesión. `acusacion_constitucional` and `informe_cei` were added when the Tabla Semanal parser surfaced rows that did not fit existing kinds (see ADR-0017 §7). Refundidos: the first cited bolet̄ín becomes `bill_id` and the `external_ref` key; the rest are listed in `description`. Unknown bolet̄ínes upsert as orphan events (`bill_id=None` + WARNING log); re-runs after bill ingestion catches up resolve the link via the same `external_ref`. Events persist after their date — cancellation is curator messaging in the title (e.g. `[CANCELADO]`), not a stored status. All writes (admin form *and* the Tabla Semanal CLI *and* future scrapers) go through `upsert_calendar_event` in `services/write.py`.
_Avoid_: treating `CalendarEvent` as an exhaustive feed of every Sesión (that's `LegislativeSession`'s job when the meeting scraper ships); using `CalendarEvent` to record past-tense bill activity (that's `BillEvent`); reintroducing a status enum without an upstream signal that needs it; bypassing `upsert_calendar_event` from the admin form; conflating `acusacion_constitucional` with `interpelacion` (interpelación questions a sitting minister; acusación is constitutional impeachment); emitting one row per refundido bolet̄ín; skipping rows whose bolet̄ín is not yet in `bills`

**Political Party**:
A registered party in the Chilean Congress. `PoliticalParty.name` is the full official name; `PoliticalParty.abbreviation` is the short identifier (e.g. "PS", "RN"). OpenData Camara is the authoritative source — `abbreviation` is set from the `Alias` field in the upstream XML. Senado returns only an abbreviation (e.g. "P.S.") and is used for lookup only, never to create party records.
_Avoid_: creating party records from Senado data

**Independent legislator**:
A legislator with no party affiliation on the relevant term — `current_party` is null (no open `LegislatorTerm` with a party today) or `party_on(date)` is null (no covering term carried a party at that date). Senado signals this via "Independiente" (and variants) in the `PARTIDO` field. This is not a party — it means unaffiliated. In the UI, independents are displayed with the label "Ind." (badge/list) or "Independiente" (full name), and the color `#6b7280` (gray).
_Avoid_: "Independientes" as a party name; treating null party as missing data; reading a stored `Legislator.party_id` (removed by ADR-0015)

**Vote-time party**:
The party / chamber shown on a vote row is read from the `LegislatorTerm` whose date window covers `VotingSession.voting_date`, not from `Legislator.current_party`. Voting-session-detail responses go through `voting.build_vote_details`, which uses `Legislator.party_on(voting_date)` and `chamber_type_on(voting_date)`. Roster, profile, and dashboard reads continue to use `current_*` (today's term) because their question is "who they are now", not "how they voted then". See ADR-0015.
_Avoid_: rendering historical votes through today's active term — closed terms turn into spurious independents; reusing `current_party` for any per-vote display

**Legislator identity**:
A `Legislator` is the person, not the seat. One `Legislator` entity represents a single physical parliamentarian regardless of how many chambers or stints they have served — a senator who was previously a deputy is one `Legislator`, not two. Per-stint facts (chamber, party, district/circumscription, the upstream chamber bridge ID) live on `LegislatorTerm` rows; the `Legislator` row itself carries only person-level data (name, gender, birth date, photo, biographic enrichment) plus `bcn_uri` as the cross-chamber identity column. See ADR-0015.
_Avoid_: creating a second `Legislator` row when the same person appears under a different chamber's upstream ID; reading a stored `Legislator.chamber_type` / `party_id` / `district_id` / `circumscription_id` / `is_active` / `bcn_id` (all removed — use the `current_*` properties or join `LegislatorTerm`)

**Chamber bridge ID**:
The chamber-side upstream identifier used to resolve a vote payload to a `Legislator`. Format is `camara:{OpenData Id}` for deputy stints and `senado:{ID_PARLAMENTARIO}` for senate stints. Stored on `LegislatorTerm.chamber_external_id` because the bridge is valid only during that chamber stint — a person who served in both chambers carries two different bridges across two terms. Vote resolution joins `LegislatorTerm.chamber_external_id` with a date window covering `VotingSession.voting_date`; unknown bridges produce orphan `Vote` rows that the reconciler claims when the matching term arrives. See ADR-0015.
_Avoid_: storing the bridge on `Legislator`; assuming a person has exactly one bridge for their lifetime

**Active legislator**:
A legislator currently serving in their chamber. `Legislator.is_active` is now a **derived property** — true iff the legislator has a `LegislatorTerm` whose date window covers today (see ADR-0015). The stored flag is gone; chamber/party/district/circumscription are likewise read from the active term via `current_chamber_type`, `current_party`, `current_district`, `current_circumscription`. The Pydantic schemas preserve the JSON contract via `validation_alias`. Used by the dashboard chamber-composition counts and by the `/legisladores` listing endpoint as the default scope (filters translate to `LegislatorTerm` joins). Inactive legislators (no open term) are historical and excluded by default from user-facing rosters.
_Avoid_: deriving "active" from `Estado`, from senado.cl hemicycle membership, or from BCN SPARQL `PositionPeriod` filtering; relying on a stored `Legislator.is_active`/`chamber_type`/`party_id` column (all removed)

**Senator roster source**:
The authoritative source for senator identity and chamber history is the senado.cl `web-back` historical catalog (`api/hemicycle?limit=1000` with **no** `vigentes` filter), called via `SenadoWebClient.get_historical_catalog`. Each record carries a `PERIODOS` array with the senator's full chamber history (S and D periods with `DESDE`/`HASTA` year boundaries and a `VIGENTE` flag on the current term); each period becomes one `LegislatorTerm`. Records with `PERIODOS == []` are duplicate stubs under a different `ID_PARLAMENTARIO` and are dropped client-side — keeping them would create a cross-chamber duplicate `Legislator` (ADR-0015). BCN REST `ObtenerParlamentariosActivos` is retained as the source for `bcn_uri` + `bcn_wiki_url`, dispatched as enrichment keyed by the chamber bridge (`senado:{PARLID}` / `camara:{Id}`). BCN SPARQL still runs out-of-band via `python -m app.cli ingestors bcn-sparql-enrichment` for profession, twitter, appointment URIs, photo, keyed by `bcn_uri`. See ADR-0012, ADR-0015.
_Avoid_: BCN SPARQL `get_active_appointments` as roster authority; `senadores_vigentes.php` as roster authority; consuming an empty-`PERIODOS` hemicycle record as a separate person

**BCN biographic source**:
BCN exposes two endpoints with distinct roles. (1) **BCN REST** `ObtenerParlamentariosActivos` is the active-roster authority for both chambers (see *Active legislator* and *Senator roster source*) and also supplies `bcn_uri` (from the `<Parlamentario @uri>` attribute) and `bcn_wiki_url` (derived from `IdWiki` as `https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki/{IdWiki}`) on every run — these columns no longer depend on SPARQL availability. (2) **BCN SPARQL** (`datos.bcn.cl/sparql`) is out-of-band enrichment for profession, twitter handle, authoritative photo, and `ParliamentaryAppointment` history across both chambers, run via `python -m app.cli ingestors bcn-sparql-enrichment` when SPARQL is healthy. Deputy SPARQL enrichment joins via `bcnbio:idCamaraDeDiputados` == OpenData deputy `Id`; senator SPARQL enrichment via `bcnbio:idSenado` == senado `PARLID`. BCN never creates `PoliticalParty` records and never overwrites name, party, district, or circumscription set by the chamber sources. See ADR-0012.
_Avoid_: depending on BCN SPARQL for fields that BCN REST exposes (`bcn_uri`, `bcn_wiki_url`, district/circumscription, party); scraping camara.cl or senado.cl for fields BCN already exposes

**Parliamentary appointment**:
*(retired)* The `parliamentary_appointments` table no longer exists. As of ADR-0015 it has been collapsed into `LegislatorTerm`, which now carries every chamber-stint fact: chamber, party (nullable), district/circumscription, dates, and — when SPARQL enrichment runs — the BCN `PositionPeriod` URI on `LegislatorTerm.bcn_appointment_uri` (nullable, unique). Reconstruct an "appointment span" by grouping contiguous terms that share `bcn_appointment_uri` or `(chamber_id, chamber_external_id)`.
_Avoid_: writing to or expecting a `parliamentary_appointments` table; treating party-change windows as separate appointments

**Deputy district source**:
BCN REST `ObtenerParlamentariosActivos` is authoritative: deputy rows carry `RepresentacionGeografica/DivisionPoliticoAdministrativa[@tipo="Distrito"]/descripcion` (e.g. `"Distrito N° 8"`), which `LegislatorParser.parse_bcn_rest_deputy` parses into the integer used to look up `District.number`. The camara.cl scraper is narrowed to photo + profile_url enrichment only (Cloudflare-protected, via the stealth `ScraperEngine`); it matches existing deputies by `camara:{dipid}` and never creates legislators or touches party/district data. See ADR-0012.
_Avoid_: expecting district from OpenData/wscamaradiputados (always empty); scraping camara.cl for district

**Bills discovery source**:
The authoritative discovery feed for bills is the `restsil.senado.cl/v3/buscarProyectosDeLey` paged backend (a non-public endpoint scraped from `portallegislativo.senado.cl`, apikey-authenticated). The current-year window is rescanned all-statuses on every tick; past years are swept once per day with `estado=T` only. Full bill detail (tramitaciones, etapas, informes, oficios, comparados, materias) still comes from wspublico `tramitacion.php?boletin=X` — restsil only exposes summaries. The OpenData Cámara year-scan (`get_mensajes_x_anno` / `get_mociones_x_anno`) is retained as a failover, selected via `INGESTOR_BILLS_SOURCE=opendata`. See ADR-0013.
_Avoid_: treating `PROYFECHAINGRESO` as a modified-since signal; expecting full bill detail from restsil

**Voting session source**:
Both chambers ingest votes via dedicated paged-feed tasks watermarked by an upstream monotonic id, decoupled from bill discovery. Senate: `run_ingest_senate_votes` walks `restsil.senado.cl/v3/buscarVotaciones?order=desc&sort=HORA`; each row carries full per-legislator detail in one call (no per-bulletin fan-out). Senator-vote rows resolve via `legislator_external_id = senado:{PARLID}` against `Legislator.bcn_id`. `voting_type` is heuristically inferred from `TEMA`; `stage_label` is `None`. Chamber: `run_ingest_chamber_votes` walks `WSLegislativo.asmx/retornarVotacionesXAnno?prmAnno=YYYY` (current year every tick; past years on cold start only, bounded by `ingestor_bills_start_year`); the summary is light, so rich fields (`voting_type`, `Articulo`, trámites) come from a per-bulletin enrichment via `retornarVotacionesXProyectoLey?prmNumeroBoletin=X`, and per-deputy detail from `retornarVotacionDetalle?prmVotacionId=Z`. Deputy-vote rows resolve via `legislator_external_id = camara:{deputy_id}`. Watermarks live in `IngestorState(entity_type="senate_votes" | "chamber_votes").last_cursor` (highest `ID_VOTACION` / `<Id>` seen). Source flags `INGESTOR_SENATE_VOTES_SOURCE` (`restsil` | `wspublico`, default `restsil`) and `INGESTOR_CHAMBER_VOTES_SOURCE` (`bulk` | `bill_detail`, default `bulk`) each switch their chamber to a failover that runs the legacy embedded-vote path during bill ingest and no-ops the dedicated task. Votes whose bulletin is not yet in `bills` are saved with `bill_id = None` and the upstream bulletin stored on `VotingSession.bill_bulletin_number`; `upsert_bill` reconciles them deterministically when the bill arrives. Chamber non-bill votes (Descripcion without a parseable `Boletín N° X-Y`) are skipped. See ADR-0013.
_Avoid_: relying on the votes embedded in `tramitacion.php` or `retornarProyectoLey` while the bulk paths are active; the legacy `senado:vot:{bulletin}:{session}` dedup key (now `senado:vot:{ID_VOTACION}`); name-based legislator matching; assuming every chamber vote is bill-linked at write time (the orphan path defers linkage to `upsert_bill` reconciliation)
