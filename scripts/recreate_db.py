#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = PROJECT_ROOT / "migrations" / "versions"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SYSTEM_DATABASES = {"postgres", "template0", "template1"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Drop and recreate the PostgreSQL database pointed to by DATABASE_URL, "
            "delete old Alembic revisions, generate a fresh initial migration, and apply it."
        )
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL for this run.",
    )
    parser.add_argument(
        "--migration-message",
        default="initial_schema",
        help="Alembic revision message for the regenerated baseline migration.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    return parser.parse_args()


def resolve_database_url(database_url: str | None) -> str:
    if database_url:
        return database_url

    from app.core.config import settings

    return settings.database_url


def load_target_url(database_url: str) -> URL:
    target_url = make_url(database_url)
    if target_url.get_backend_name() != "postgresql":
        raise SystemExit("This script currently supports PostgreSQL DATABASE_URL values only.")
    if not target_url.database:
        raise SystemExit("DATABASE_URL must include a database name.")
    if target_url.database in SYSTEM_DATABASES:
        raise SystemExit(f"Refusing to recreate reserved PostgreSQL database '{target_url.database}'.")
    return target_url


def build_admin_url(target_url: URL) -> URL:
    return target_url.set(database="postgres")


def describe_target(target_url: URL) -> str:
    host = target_url.host or "localhost"
    port = target_url.port or 5432
    user = target_url.username or "<current-user>"
    return f"{user}@{host}:{port}/{target_url.database}"


def confirm_reset(target_url: URL, assume_yes: bool) -> None:
    if assume_yes:
        return

    if not sys.stdin.isatty():
        raise SystemExit("Refusing to recreate the database in non-interactive mode without --yes.")

    response = input(
        "This will:\n"
        f"  - DROP and recreate the database '{describe_target(target_url)}'\n"
        f"  - Delete existing Alembic revisions in '{VERSIONS_DIR.relative_to(PROJECT_ROOT)}'\n"
        "  - Generate a fresh initial migration and apply it\n"
        "Continue? [y/N]: "
    )
    if response.strip().lower() not in {"y", "yes"}:
        raise SystemExit("Cancelled.")


def recreate_database(target_url: URL) -> None:
    admin_url = build_admin_url(target_url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    database_name = target_url.database
    if database_name is None:
        raise SystemExit("DATABASE_URL must include a database name.")
    quoted_database_name = admin_engine.dialect.identifier_preparer.quote(database_name)

    try:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_database_name}"))
            connection.execute(text(f"CREATE DATABASE {quoted_database_name}"))
    finally:
        admin_engine.dispose()


def clear_version_history() -> list[str]:
    if not VERSIONS_DIR.exists():
        raise SystemExit(f"Alembic versions directory does not exist: {VERSIONS_DIR}")

    removed_entries: list[str] = []
    for path in sorted(VERSIONS_DIR.iterdir()):
        if path.name == "__pycache__":
            shutil.rmtree(path)
            removed_entries.append(path.name)
            continue
        if not path.is_file() or path.suffix != ".py":
            continue
        path.unlink()
        removed_entries.append(path.name)
    return removed_entries


def list_revision_files() -> set[str]:
    return {path.name for path in VERSIONS_DIR.glob("*.py") if path.is_file()}


def run_alembic(*args: str, database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )


def regenerate_migration(database_url: str, message: str) -> str:
    before_files = list_revision_files()
    run_alembic("revision", "--autogenerate", "-m", message, database_url=database_url)
    after_files = list_revision_files()
    created_files = sorted(after_files - before_files)
    if not created_files:
        raise SystemExit("Alembic did not create a new revision file.")
    run_alembic("upgrade", "head", database_url=database_url)
    return created_files[-1]


def main() -> None:
    args = parse_args()
    database_url = resolve_database_url(args.database_url)
    target_url = load_target_url(database_url)
    confirm_reset(target_url, args.yes)

    print(f"Recreating database: {describe_target(target_url)}")
    print("Dropping existing database and creating a fresh one...")

    try:
        recreate_database(target_url)
        removed_entries = clear_version_history()
        print(f"Deleted {len(removed_entries)} existing Alembic revision file(s).")
        created_revision = regenerate_migration(database_url, args.migration_message)
    except SQLAlchemyError as exc:
        raise SystemExit(f"Database recreation failed: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        command = " ".join(str(part) for part in exc.cmd)
        raise SystemExit(f"Alembic command failed: {command}") from exc

    print(f"Created and applied Alembic revision: {created_revision}")


if __name__ == "__main__":
    main()