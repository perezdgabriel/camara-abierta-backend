#!/usr/bin/env python3
"""Seed initial party bloc affiliations for the majority simulator (ADR-0006).

Bloc alignment (oficialismo / oposición) is *editorial*: no congress API exposes
it. This script encodes a best-effort starting mapping for the current
government period, keyed by party abbreviation, and upserts one open-ended
``BlocAffiliation`` per party found in the database. It is idempotent — safe to
re-run.

Parties NOT in the mapping below (e.g. ambiguous centre parties, or parties not
yet classified) are intentionally left unassigned: their members surface in the
simulator's "sin alinear" tray until an editor assigns a bloc via the admin
panel. Review and correct this mapping in the admin panel — it is a default, not
ground truth.

Usage:
    uv run python scripts/seed_blocs.py
    uv run python scripts/seed_blocs.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.session import task_session  # noqa: E402
from app.models.enums import Bloc  # noqa: E402
from app.models.legislature import PoliticalParty  # noqa: E402
from app.services.write import upsert_bloc_affiliation  # noqa: E402

# Start of the current government period (Boric, 2022-03-11). Used as the
# affiliation start_date so the rows model "this is the alignment for this
# period". When the government changes, close these rows (set end_date) and seed
# a new period rather than mutating these.
PERIOD_START = date(2022, 3, 11)

# Editorial best-effort mapping by party abbreviation. See module docstring.
# Abbreviations mirror PoliticalParty.abbreviation (set from OpenData `Alias`).
PARTY_BLOC: dict[str, Bloc] = {
    # ── Oficialismo (governing coalition) ──
    "PC": Bloc.OPOSICION,  # Partido Comunista
    "FA": Bloc.OPOSICION,  # Frente Amplio
    "RD": Bloc.OPOSICION,  # Revolución Democrática (now within FA)
    "COMUNES": Bloc.OPOSICION,  # Comunes (within FA)
    "PCS": Bloc.OPOSICION,  # Convergencia Social (within FA)
    "PS": Bloc.OPOSICION,  # Partido Socialista
    "PPD": Bloc.OPOSICION,  # Partido Por la Democracia
    "PR": Bloc.OPOSICION,  # Partido Radical
    "FRVS": Bloc.OPOSICION,  # Federación Regionalista Verde Social
    "PL": Bloc.OPOSICION,  # Partido Liberal
    "PAH": Bloc.OPOSICION,  # Acción Humanista
    # ── Oposición ──
    "UDI": Bloc.OFICIALISMO,  # Unión Demócrata Independiente
    "RN": Bloc.OFICIALISMO,  # Renovación Nacional
    "EVOP": Bloc.OFICIALISMO,  # Evópoli
    "PREP": Bloc.OFICIALISMO,  # Partido Republicano
    "PSC": Bloc.OFICIALISMO,  # Partido Social Cristiano
    "PNL": Bloc.OFICIALISMO,  # Partido Nacional Libertario
    "PCC": Bloc.OFICIALISMO,
    # "DEM": Bloc.OFICIALISMO,  # Demócratas
    # "PDG" : Bloc.OFICIALISMO,  # Partido de la Gente
    # Deliberately unmapped (ambiguous / unclassified → "sin alinear" tray):
    # DC (Demócrata Cristiano), PH (Partido Humanista), PRI, PRO, PCC, PRSD…
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeded = 0
    skipped: list[str] = []

    with task_session() as db:
        parties = db.execute(select(PoliticalParty)).scalars().all()
        present = {p.abbreviation.upper(): p for p in parties}

        for abbreviation, bloc in PARTY_BLOC.items():
            party = present.get(abbreviation.upper())
            if party is None:
                skipped.append(abbreviation)
                continue
            if args.dry_run:
                print(f"  would set {abbreviation:<8} → {bloc.value}")
            else:
                upsert_bloc_affiliation(
                    db,
                    party_id=party.id,
                    bloc=bloc,
                    start_date=PERIOD_START,
                )
            seeded += 1

        unmapped = sorted(
            p.abbreviation for p in parties if p.abbreviation.upper() not in PARTY_BLOC
        )

        if args.dry_run:
            db.rollback()

    print(f"\nBloc affiliations {'(dry-run) ' if args.dry_run else ''}seeded: {seeded}")
    if skipped:
        print(f"In mapping but not found in DB: {', '.join(skipped)}")
    if unmapped:
        print(
            "Parties present in DB but left unaligned "
            f"(sin alinear): {', '.join(unmapped)}"
        )


if __name__ == "__main__":
    main()
