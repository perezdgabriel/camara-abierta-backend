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
A legislator currently serving in their chamber. `Legislator.is_active` is the canonical flag. It is derived for both chambers from BCN's `bcnbio:hasParliamentaryAppointment` graph: a person is active iff they have a `PositionPeriod` whose `hasEnd → originalDate` is on or after today. The upstream `Estado` flag and the senado.cl hemicycle seated set are no longer consulted because both proved stale (ADR-0005). Used by the dashboard chamber-composition counts and by the `/legisladores` listing endpoint as the default scope. Inactive legislators are historical and are excluded by default from user-facing rosters.
_Avoid_: deriving "active" from `Estado` or from senado.cl hemicycle membership

**Senator roster source**:
The authoritative source for the *list* of sitting senators is the BCN linked-open-data SPARQL endpoint (`datos.bcn.cl/sparql`), filtering active `PositionPeriod` nodes by `hasEnd >= today`. The senado.cl `web-back` JSON catalog provides only metadata (circumscription, region, party abbreviation, email, phone, photo) joined by `bcnbio:idSenado` == `PARLID`. The hemicycle seated set on senado.cl is not trusted. wspublico XML remains the source for bills, votes, and committees only. See ADR-0005 (which supersedes ADR-0002).
_Avoid_: senado.cl `api/hemicycle` or `senadores_vigentes.php` for the senator roster

**BCN biographic source**:
The BCN SPARQL endpoint is the canonical enrichment source for profession, twitter handle, BCN wiki page URL (`bcn_wiki_url`), and authoritative photo across both chambers. Deputy enrichment joins via `bcnbio:idCamaraDeDiputados` == OpenData deputy `Id`; senator enrichment via `bcnbio:idSenado` == senado `PARLID`. BCN never creates `PoliticalParty` records (ADR-0001 unchanged) and never overwrites name, party, district, or circumscription set by the chamber sources. `Legislator.bcn_uri` stores the canonical person URI for re-querying.
_Avoid_: scraping camara.cl or senado.cl for fields BCN already exposes

**Parliamentary appointment**:
A formal, dated record that a legislator served in a chamber from a `start_date` to an `end_date`. One row in `parliamentary_appointments` per BCN `PositionPeriod` (the URI stored in `bcn_appointment_uri` is the upsert key). Used to render term history. Distinct from `LegislatorTerm` (party-membership windows from OpenData militancias): a single appointment can contain multiple `LegislatorTerm` rows when the legislator changes party mid-term.
_Avoid_: conflating with `LegislatorTerm`; treating party-change rows as separate appointments

**Deputy district source**:
No congress API exposes the deputy→district link, so it is scraped from camara.cl (Cloudflare-protected; via the stealth `ScraperEngine`). The scrape is *enrichment-only*: it matches existing deputies by `camara:{dipid}` and sets `district_id` (plus photo/profile), never creating legislators or touching party data. See ADR-0003.
_Avoid_: expecting district from OpenData/wscamaradiputados (always empty)

**Bills discovery source**:
The authoritative discovery feed for bills is the `restsil.senado.cl/v3/buscarProyectosDeLey` paged backend (a non-public endpoint scraped from `portallegislativo.senado.cl`, apikey-authenticated). The current-year window is rescanned all-statuses on every tick; past years are swept once per day with `estado=T` only. Full bill detail (tramitaciones, etapas, informes, oficios, comparados, materias) still comes from wspublico `tramitacion.php?boletin=X` — restsil only exposes summaries. The OpenData Cámara year-scan (`get_mensajes_x_anno` / `get_mociones_x_anno`) is retained as a failover, selected via `INGESTOR_BILLS_SOURCE=opendata`. See ADR-0009 (which supersedes ADR-0008's discovery half).
_Avoid_: treating `PROYFECHAINGRESO` as a modified-since signal; expecting full bill detail from restsil

**Voting session source**:
Senate voting sessions are captured by a dedicated `run_ingest_senate_votes` task that walks the `restsil.senado.cl/v3/buscarVotaciones?order=desc&sort=HORA` paged feed and stops at a watermark (highest `ID_VOTACION` seen, stored in `IngestorState(entity_type="senate_votes").last_cursor`). Each restsil row carries the full per-legislator detail in one call, so there is no per-bulletin fan-out. Senator-vote rows resolve to existing legislators via `legislator_external_id = senado:{PARLID}` against `Legislator.bcn_id`, bypassing the legacy name match. `voting_type` is heuristically inferred from `TEMA` (the upstream dropped `TIPOVOTACION`); `stage_label` is `None` for restsil-sourced votes. Chamber votes still flow via OpenData enrichment on the bills ingest path. The legacy `votaciones.php` per-bulletin path stays in tree as a failover, selected via `INGESTOR_SENATE_VOTES_SOURCE=wspublico` (which makes the dedicated task no-op and revives the embedded vote capture on bill ingest). See ADR-0009 (which supersedes ADR-0008's vote-capture half).
_Avoid_: relying on the votes embedded in `tramitacion.php`; the legacy `senado:vot:{bulletin}:{session}` dedup key (now `senado:vot:{ID_VOTACION}`); name-based legislator matching
