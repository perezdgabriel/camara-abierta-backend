#!/usr/bin/env python3
"""Seed the curated generic Topic vocabulary (ADR-0021).

Topics are no longer ingested from upstream "materias" — they're assigned by
an LLM classifying each bill's full text (see ``app/services/llm.py``,
``app/tasks/bills.py:_generate_proposal_layer``). The LLM is instructed to
prefer reusing an existing topic over coining a new one, so seeding a
sensible starting vocabulary here anchors that convergence instead of letting
naming drift depend on whichever bill happens to be classified first. It is
idempotent — safe to re-run. New topics can still appear later (LLM-coined or
added via the admin panel).

Usage:
    uv run python scripts/seed_topics.py
    uv run python scripts/seed_topics.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.session import task_session  # noqa: E402
from app.services.write import upsert_topic  # noqa: E402

CURATED_TOPICS: list[str] = [
    "Trabajo",
    "Salud",
    "Seguridad",
    "Educación",
    "Vivienda",
    "Medio Ambiente",
    "Pensiones",
    "Tributaria",
    "Género",
    "Justicia",
    "Agricultura y Pesca",
    "Energía",
    "Relaciones Exteriores",
    "Cultura",
    "Deportes",
    "Tecnología",
    "Municipal",
    "Transporte",
]


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

    with task_session() as db:
        for name in CURATED_TOPICS:
            if args.dry_run:
                print(f"  would seed {name}")
            else:
                upsert_topic(db, name)
            seeded += 1

        if args.dry_run:
            db.rollback()

    print(f"\nTopics {'(dry-run) ' if args.dry_run else ''}seeded: {seeded}")


if __name__ == "__main__":
    main()
