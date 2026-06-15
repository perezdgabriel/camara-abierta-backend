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
- `VoteChoice`: for, against, abstain, paired, dispensed, absent
- `VotingResult`: approved, rejected, tie
- `ChamberType`: deputies, senate
- `CommitteeType`: permanent, special, investigative, mixed

**Sponsoring ministries**:
Optional zero-to-many ministries attached to a bill as upstream metadata from the Chamber of Deputies. They are historical bill-scoped labels, not a shared platform-wide ministry catalog.
_Avoid_: ministries, ministry catalog

**Dispensed vote**:
An excused legislator vote choice recorded explicitly by an upstream chamber voting source. It is distinct from an absence.
_Avoid_: absent

**Bill lifecycle**:
`Bill.status` is the source of truth from the upstream Congress API, not derived from stages. `BillStage` records are the detailed legislative history. The `is_current` flag on `BillStage` tracks where the bill currently is in the process.

**Bill activity**:
`BillEvent` is the granular legislative activity log for a bill, derived from upstream `tramitaciones`. `BillStage` remains the coarser progression model used for current-stage tracking. `last_activity_date` means the latest `BillEvent.event_date`, falling back to `Bill.entry_date` when a bill has no events.

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
A core feature. Legislative language and official gazette content are simplified for the general public. Bills get `ai_summary`. Normas get executive summaries, key points, beneficiaries, and citizen importance ratings. LLM providers (Gemini, OpenWebUI/Ollama) are interchangeable — the strategy is to minimize cost.

**Geography**:
Chile's administrative and electoral geography: `Region`, `Province`, `Commune` (administrative), `District` (Chamber of Deputies), `Circumscription` (Senate). Users find their representatives by selecting their commune, which maps to both a district and a circumscription. The authoritative current baseline is the checked-in dataset at `app/geography/data/chile_current.json`, loaded manually with `python -m app.cli geography` and applied atomically via `apply_geography_dataset`. The live `reference-data` ingest is topic-only; geography is no longer fetched from OpenData. `IngestorState(entity_type="geography")` stores the applied dataset version in `last_cursor`.

**Search**:
Elasticsearch indexes bills for full-text search across titles, summaries, and full text. Other domains (normas, reglamentos) use filtered DB queries.

**Notifications**:
A core feature (not yet implemented). Users will subscribe to bills, representatives, topics, etc. and receive updates. Currently only internal email alerts exist via Resend.

**Admin panel**:
Internal tool (`sqladmin`) for manual data management. Used to update reference data or fix records when data collectors can't capture them.

**Topics**:
Hierarchical tags for bills (e.g. "Education" → "Higher Education"). Pre-defined reference data, but new topics can appear when fetching bills from upstream APIs. Also creatable via the admin panel.

**Political Party**:
A registered party in the Chilean Congress. `PoliticalParty.name` is the full official name; `PoliticalParty.abbreviation` is the short identifier (e.g. "PS", "RN"). OpenData Camara is the authoritative source — `abbreviation` is set from the `Alias` field in the upstream XML. Senado returns only an abbreviation (e.g. "P.S.") and is used for lookup only, never to create party records.
_Avoid_: creating party records from Senado data

**Independent legislator**:
A legislator with no current party affiliation. `Legislator.party_id` is null. Senado signals this via "Independiente" (and variants) in the `PARTIDO` field. This is not a party — it means unaffiliated. In the UI, independents are displayed with the label "Ind." (badge/list) or "Independiente" (full name), and the color `#6b7280` (gray).
_Avoid_: "Independientes" as a party name; treating null party as missing data

**Active legislator**:
A legislator currently serving in their chamber. `Legislator.is_active` is the canonical flag. It is set for both chambers from BCN's `ObtenerParlamentariosActivos` REST endpoint (`datos.bcn.cl/catalogo/servicio/ServiciosWebHistoriaDeLaLey/...`): every record returned is currently seated, partitioned into senate (`Camara/Id=261`) and deputies (`Camara/Id=288`). The upstream `Estado` flag and the senado.cl hemicycle seated set are no longer consulted (both proved stale); BCN's SPARQL appointment-graph derivation has been replaced by the REST roster. See ADR-0012. Used by the dashboard chamber-composition counts and by the `/legisladores` listing endpoint as the default scope. Inactive legislators are historical and are excluded by default from user-facing rosters.
_Avoid_: deriving "active" from `Estado`, from senado.cl hemicycle membership, or from BCN SPARQL `PositionPeriod` filtering

**Senator roster source**:
The authoritative source for the *list* of sitting senators is the BCN REST endpoint `ObtenerParlamentariosActivos` (`datos.bcn.cl/catalogo/servicio/ServiciosWebHistoriaDeLaLey/...`), filtered to `Camara/Id=261`. `IdEnCamaraDeOrigen` bridges to senado.cl `ID_PARLAMENTARIO`, so `Legislator.bcn_id = senado:{PARLID}` reconciles cleanly. The senado.cl `web-back` JSON catalog provides senator-only metadata (gender, phone, photo URLs, slug-derived profile URL) by overlaying onto the BCN REST record, joined by the same PARLID. BCN SPARQL is no longer on the roster path — it runs out-of-band via `python -m app.cli ingestors bcn-sparql-enrichment` for profession, twitter, and `ParliamentaryAppointment` history. wspublico XML remains the source for bills, votes, and committees only. See ADR-0012.
_Avoid_: BCN SPARQL `get_active_appointments` as roster authority; senado.cl `api/hemicycle` or `senadores_vigentes.php` as roster authority

**BCN biographic source**:
BCN exposes two endpoints with distinct roles. (1) **BCN REST** `ObtenerParlamentariosActivos` is the active-roster authority for both chambers (see *Active legislator* and *Senator roster source*) and also supplies `bcn_uri` (from the `<Parlamentario @uri>` attribute) and `bcn_wiki_url` (derived from `IdWiki` as `https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki/{IdWiki}`) on every run — these columns no longer depend on SPARQL availability. (2) **BCN SPARQL** (`datos.bcn.cl/sparql`) is out-of-band enrichment for profession, twitter handle, authoritative photo, and `ParliamentaryAppointment` history across both chambers, run via `python -m app.cli ingestors bcn-sparql-enrichment` when SPARQL is healthy. Deputy SPARQL enrichment joins via `bcnbio:idCamaraDeDiputados` == OpenData deputy `Id`; senator SPARQL enrichment via `bcnbio:idSenado` == senado `PARLID`. BCN never creates `PoliticalParty` records and never overwrites name, party, district, or circumscription set by the chamber sources. See ADR-0012.
_Avoid_: depending on BCN SPARQL for fields that BCN REST exposes (`bcn_uri`, `bcn_wiki_url`, district/circumscription, party); scraping camara.cl or senado.cl for fields BCN already exposes

**Parliamentary appointment**:
A formal, dated record that a legislator served in a chamber from a `start_date` to an `end_date`. One row in `parliamentary_appointments` per BCN `PositionPeriod` (the URI stored in `bcn_appointment_uri` is the upsert key). Used to render term history. Distinct from `LegislatorTerm` (party-membership windows from OpenData militancias): a single appointment can contain multiple `LegislatorTerm` rows when the legislator changes party mid-term.
_Avoid_: conflating with `LegislatorTerm`; treating party-change rows as separate appointments

**Deputy district source**:
BCN REST `ObtenerParlamentariosActivos` is authoritative: deputy rows carry `RepresentacionGeografica/DivisionPoliticoAdministrativa[@tipo="Distrito"]/descripcion` (e.g. `"Distrito N° 8"`), which `LegislatorParser.parse_bcn_rest_deputy` parses into the integer used to look up `District.number`. The camara.cl scraper is narrowed to photo + profile_url enrichment only (Cloudflare-protected, via the stealth `ScraperEngine`); it matches existing deputies by `camara:{dipid}` and never creates legislators or touches party/district data. See ADR-0012.
_Avoid_: expecting district from OpenData/wscamaradiputados (always empty); scraping camara.cl for district

**Bills discovery source**:
The authoritative discovery feed for bills is the `restsil.senado.cl/v3/buscarProyectosDeLey` paged backend (a non-public endpoint scraped from `portallegislativo.senado.cl`, apikey-authenticated). The current-year window is rescanned all-statuses on every tick; past years are swept once per day with `estado=T` only. Full bill detail (tramitaciones, etapas, informes, oficios, comparados, materias) still comes from wspublico `tramitacion.php?boletin=X` — restsil only exposes summaries. The OpenData Cámara year-scan (`get_mensajes_x_anno` / `get_mociones_x_anno`) is retained as a failover, selected via `INGESTOR_BILLS_SOURCE=opendata`. See ADR-0013.
_Avoid_: treating `PROYFECHAINGRESO` as a modified-since signal; expecting full bill detail from restsil

**Voting session source**:
Both chambers ingest votes via dedicated paged-feed tasks watermarked by an upstream monotonic id, decoupled from bill discovery. Senate: `run_ingest_senate_votes` walks `restsil.senado.cl/v3/buscarVotaciones?order=desc&sort=HORA`; each row carries full per-legislator detail in one call (no per-bulletin fan-out). Senator-vote rows resolve via `legislator_external_id = senado:{PARLID}` against `Legislator.bcn_id`. `voting_type` is heuristically inferred from `TEMA`; `stage_label` is `None`. Chamber: `run_ingest_chamber_votes` walks `WSLegislativo.asmx/retornarVotacionesXAnno?prmAnno=YYYY` (current year every tick; past years on cold start only, bounded by `ingestor_bills_start_year`); the summary is light, so rich fields (`voting_type`, `Articulo`, trámites) come from a per-bulletin enrichment via `retornarVotacionesXProyectoLey?prmNumeroBoletin=X`, and per-deputy detail from `retornarVotacionDetalle?prmVotacionId=Z`. Deputy-vote rows resolve via `legislator_external_id = camara:{deputy_id}`. Watermarks live in `IngestorState(entity_type="senate_votes" | "chamber_votes").last_cursor` (highest `ID_VOTACION` / `<Id>` seen). Source flags `INGESTOR_SENATE_VOTES_SOURCE` (`restsil` | `wspublico`, default `restsil`) and `INGESTOR_CHAMBER_VOTES_SOURCE` (`bulk` | `bill_detail`, default `bulk`) each switch their chamber to a failover that runs the legacy embedded-vote path during bill ingest and no-ops the dedicated task. Votes whose bulletin is not yet in `bills` are saved with `bill_id = None` and the upstream bulletin stored on `VotingSession.bill_bulletin_number`; `upsert_bill` reconciles them deterministically when the bill arrives. Chamber non-bill votes (Descripcion without a parseable `Boletín N° X-Y`) are skipped. See ADR-0013.
_Avoid_: relying on the votes embedded in `tramitacion.php` or `retornarProyectoLey` while the bulk paths are active; the legacy `senado:vot:{bulletin}:{session}` dedup key (now `senado:vot:{ID_VOTACION}`); name-based legislator matching; assuming every chamber vote is bill-linked at write time (the orphan path defers linkage to `upsert_bill` reconciliation)
