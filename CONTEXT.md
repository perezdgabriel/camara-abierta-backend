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
Chile's administrative and electoral geography: `Region`, `Province`, `Commune` (administrative), `District` (Chamber of Deputies), `Circumscription` (Senate). Users find their representatives by selecting their commune, which maps to a district and circumscription.

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
A legislator currently serving in their chamber. `Legislator.is_active` is the canonical flag, sourced from the OpenData Camara upstream `Estado` element. Used by the dashboard chamber-composition counts and by the `/legisladores` listing endpoint as the default scope. Inactive legislators are historical and are excluded by default from user-facing rosters.
_Avoid_: deriving "active" from term dates when `is_active` is available
