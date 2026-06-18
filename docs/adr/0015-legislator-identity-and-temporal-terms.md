# Legislator identity and per-stint temporal terms

**Status:** Accepted, 2026-06-17. Supersedes the ``ParliamentaryAppointment``
paragraph of [ADR-0012](0012-legislator-ingest-pipeline.md) and reshapes the
chamber-vote resolution path discussed there.

## Context

A current senator who previously served as a deputy was being **duplicated**
in the database whenever an old chamber vote was ingested. The duplicate row
had ``party_id = None``, ``is_active = False``, and the deputy-side chamber
bridge (``camara:{dipid}``), while the canonical row held the senator-side
bridge (``senado:{PARLID}``). Both rows referred to the same physical person.

Root cause was structural: ``Legislator.bcn_id`` was ``UNIQUE`` and carried
exactly one chamber bridge, so a single legislator could not be reachable by
both ``camara:1234`` and ``senado:999`` simultaneously. The vote resolver at
``app/services/write.py`` followed three steps:

1. ``Legislator.bcn_id == legislator_external_id`` — direct match.
2. ``_find_legislator_by_name`` scoped to the **incoming vote's** chamber.
3. Fall back to creating a brand-new placeholder ``Legislator`` with
   ``party_id = None``.

A senator's ``Legislator.chamber_type = SENATE`` was invisible to step 2 for
a chamber-of-deputies vote, so the placeholder path always fired. The
duplication was baked into the schema.

Two further problems flowed from the same column shape:

- ``LegislatorTerm`` already existed as a per-stint party-window table
  populated from OpenData militancias, but it was only written for *current
  deputies* and pegged its ``chamber`` to ``legislator.chamber_type`` — so it
  could never carry a senator's deputy-era stints.
- ``ParliamentaryAppointment`` was a parallel per-stint chamber-window table
  populated by the out-of-band BCN SPARQL CLI. It overlapped structurally
  with ``LegislatorTerm`` (both held ``legislator_id, chamber_id, start, end``)
  but the two tables were not linked, and the split was a consequence of
  upstream source granularity, not domain need.

The intended outcome is to make ``Legislator`` represent the **person** —
identity-stable across chambers and stints — and move every chamber-scoped
fact onto dated ``LegislatorTerm`` rows. This both eliminates the duplication
bug and gives the downstream voting signals a real per-date party answer
instead of falling back to ``None``.

## Decisions

### 1. One ``Legislator`` row per physical person

The senator who used to be a deputy is one ``Legislator`` with multiple
``LegislatorTerm`` rows. ``Legislator`` carries only person-level columns
(name, gender, birth date, biographic enrichment, social handles,
``default_bloc``) plus ``bcn_uri`` as the cross-chamber identity.

### 2. ``ParliamentaryAppointment`` collapses into ``LegislatorTerm``

``LegislatorTerm`` becomes the canonical chamber-scoped record. It already
held ``legislator_id, period_id, chamber_id, party_id, start_date, end_date,
end_reason``; the merged shape adds:

- ``chamber_external_id`` (e.g. ``camara:942``, ``senado:1335``) — the
  upstream chamber-side ID valid only during that stint. This is the new
  primary join key from votes to legislators.
- ``district_id`` (deputy stints) and ``circumscription_id`` (senate stints)
  — chamber-scoped, lifted off ``Legislator``.
- ``bcn_appointment_uri`` — the BCN ``PositionPeriod`` URI from SPARQL,
  populated when the out-of-band enrichment runs. Was previously the unique
  key of the deleted ``ParliamentaryAppointment`` table; now lives as a
  nullable unique column on the term.

A composite index on ``(chamber_external_id, start_date, end_date)`` powers
the vote-resolver hot path.

### 3. ``Legislator`` becomes strictly person-level

Drop the columns ``bcn_id``, ``chamber_type``, ``party_id``, ``district_id``,
``circumscription_id``, ``is_active``. Expose them as ``@property`` reading
the active term:

- ``current_chamber_type``, ``current_party``, ``current_party_id``,
  ``current_district``, ``current_circumscription``,
  ``current_chamber_external_id``.
- ``is_active`` is now derived: ``True`` iff there exists a term whose date
  window covers today.

The Pydantic schemas (``LegislatorSummary``, ``LegislatorBrief``) preserve
the JSON contract by using ``validation_alias`` to read from the new property
names — clients see the same ``chamber_type`` / ``party`` / ``is_active``
fields. ``bcn_uri`` remains the canonical cross-chamber identity column.

### 4. Cross-chamber person matching

When the historical ingest runs for both chambers, the same person can
appear in both feeds (OpenData ``retornarDiputados`` returns ex-deputies who
are now senators; senado.cl ``hemicycle?limit=1000`` returns senators with a
``PERIODOS`` array carrying their deputy past). The write service merges
them by:

1. ``bcn_uri`` exact match (when known — usually via BCN SPARQL link).
2. ``LegislatorTerm.chamber_external_id`` match (the person's previous-source
   bridge already in the DB).
3. Normalized ``(paternal + maternal + first name)`` match, disambiguated
   when multiple candidates exist by overlap between the seed's term windows
   and an existing candidate's terms (same chamber, overlapping dates).
4. Residual ambiguity → ``legislator_merge_candidates`` review queue
   (admin-resolved).

The legacy ``_find_legislator_by_name`` /
``_senado_vote_name_matches_legislator`` pair is **deleted**; name-match was
the fragile path that prior ADRs already flagged as unreliable for roster
decisions. It is retained only inside the explicit cross-chamber merge
helper, where it's bounded by period-overlap disambiguation and surfaces
ambiguities to admin review rather than auto-creating duplicates.

### 5. ``hemicycle?PERIODOS == []`` records are dropped

The senado.cl historical catalog returns some persons twice — once with the
full ``PERIODOS`` array and once with an empty array under a *different*
``ID_PARLAMENTARIO``. The second record's slug uses the ``-dip`` suffix and
appears to be a legacy index entry. Treating both as separate persons would
recreate exactly the cross-chamber duplicate the redesign is preventing.
``SenadoWebClient.get_historical_catalog`` drops empty-``PERIODOS`` records
client-side; ``LegislatorParser.parse_senator`` defensively returns ``None``
for the same shape.

### 6. Orphan + reconcile for unknown bridges at vote ingest

The new ``_resolve_vote_legislator`` joins ``LegislatorTerm.chamber_external_id``
with a date window:

```sql
SELECT legislator_id
  FROM legislator_terms
 WHERE chamber_external_id = $external_id
   AND start_date <= $voting_date
   AND (end_date IS NULL OR end_date >= $voting_date)
 LIMIT 1
```

When the join returns nothing, the vote is **orphaned**: ``Vote.legislator_id``
is ``NULL`` and the chamber bridge is preserved on a new
``Vote.legislator_external_id`` column. Every ``LegislatorTerm`` upsert calls
``_reconcile_orphan_votes(term)`` which retroactively links any orphan vote
whose bridge + date matches the term's window.

Schema-wise, ``Vote.legislator_id`` becomes nullable and the original
``UNIQUE (voting_session_id, legislator_id)`` constraint is replaced with two
partial unique indexes (Postgres):

- ``UNIQUE (voting_session_id, legislator_id) WHERE legislator_id IS NOT NULL``
- ``UNIQUE (voting_session_id, legislator_external_id) WHERE legislator_id IS NULL``

A daily Celery beat task (``alert_orphan_votes``, run at 05:45) counts votes
older than ``ORPHAN_VOTE_SLA_DAYS = 7`` and logs a warning if non-zero —
operator follow-up when the chamber bridge never resolves (typically means a
brand-new legislator never made it into our roster).

### 7. Historical roster endpoints replace the current-only path

``run_ingest_legislators`` now drives the term timeline from:

- **OpenData** ``retornarDiputados`` (``OpenDataCamaraClient.get_all_diputados``)
  — every person who ever served as a deputy, with the full ``Militancias``
  history. Each militancia becomes one term carrying ``camara:{Id}``.
- **senado.cl** ``hemicycle?limit=1000`` without ``vigentes=1``
  (``SenadoWebClient.get_historical_catalog``) — every senator with their
  ``PERIODOS`` chamber history. Each ``PERIODO`` becomes one term; senate
  stints carry ``senado:{ID_PARLAMENTARIO}``; deputy stints embedded in a
  senator's history leave the bridge ``None`` and are bridged by the
  cross-chamber merge against the matching OpenData deputy.

BCN REST (``ObtenerParlamentariosActivos``) keeps its role as the source for
``bcn_uri`` + ``bcn_wiki_url``, dispatched as enrichment keyed by the
chamber bridge. BCN SPARQL enrichment (profession, twitter, photo,
appointment URIs) continues to run out-of-band via
``python -m app.cli ingestors bcn-sparql-enrichment``, now keyed by
``bcn_uri`` end-to-end (matches ``Legislator.bcn_uri``).

### 8. Cutover via ``recreate-db``

Pre-release per ``CLAUDE.md``. The new schema is regenerated by
``just recreate-db``; ``IngestorState`` watermarks reset so the first cold
chamber-vote ingest re-pulls history. Any pre-existing duplicate
``Legislator`` rows from the old placeholder path are dropped wholesale —
the migration script (``script.py.mako``) produces a fresh baseline.

## Considered options

- **Alt-id table** (``legislator_external_ids(legislator_id, external_id
  UNIQUE)``) instead of putting the bridge on the term. Rejected: the bridge
  is temporally scoped by definition (a deputy ID is only valid during a
  deputy stint), and forcing the alt-id table to carry date columns would
  duplicate the term's window. The unified shape is cheaper.
- **Keep both ``LegislatorTerm`` and ``ParliamentaryAppointment`` and add a
  parent FK.** Rejected: the split was justified by ingestor convenience, not
  domain semantics; reducing the number of tables that have to agree on
  identity for the chamber-vote fix outweighs preserving the historical
  shape. Operationally, an ``appointment`` is reconstructable by grouping
  contiguous terms that share ``bcn_appointment_uri``.
- **Keep the denormalized columns on ``Legislator`` as cached "current"
  values, recomputed after term mutations.** Rejected: real drift risk
  between the cache and terms; eliminating the columns and reading from
  properties closes that hole at the cost of a one-time refactor of ~30
  read sites. Pydantic ``validation_alias`` keeps the JSON contract.
- **Move to ``LegislatorTerm.chamber_external_id`` but keep
  ``Legislator.bcn_id`` as the current chamber bridge for backwards
  compatibility.** Rejected: zombie column, two sources of truth, exactly the
  smell the refactor was meant to remove.

## Consequences

- Chamber-vote duplication is structurally impossible — the unique key
  surface no longer exists on ``Legislator``. A previously-deputy senator
  resolves through one ``LegislatorTerm`` for the deputy stint and another
  for the senate stint, both pointing at the same ``Legislator``.
- *Disciplina partidaria*, *consenso atípico*, and *inclinación de voto* can
  read accurate per-date party affiliations from the term timeline rather
  than the legacy single-column current snapshot.
- Roster ingest is meaningfully heavier — full historical pulls instead of
  current-only — but the upstream payloads are small (~hundreds of XML/JSON
  records per chamber); fetching them every cycle keeps the data fresh
  without adding a separate orchestration.
- Identity-by-name carries inherent ambiguity. The merge-review queue is the
  pressure-relief valve; operationally we expect it to be near-empty after
  the initial backfill because Chilean two-apellido naming is highly
  distinctive and the senado ``PERIODOS`` chamber-of-deputies dates pin the
  merge to the right OpenData row.
- The legacy wspublico Senate-vote failover path
  (``INGESTOR_SENATE_VOTES_SOURCE=wspublico``) no longer resolves —
  ``parse_senate_vote`` did not emit ``legislator_external_id``. The
  failover is dead in this design; the restsil path remains primary, the
  wspublico path needs to be reworked or retired in a follow-up.
- New ``legislator_merge_candidates`` table needs an admin surface for
  operator review; a default sqladmin view would suffice.
