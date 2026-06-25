"""Accent-insensitive search helpers.

PostgreSQL provides the ``unaccent`` extension; we register an equivalent
Python UDF on SQLite connections so the same query compiles on both backends.
"""

from __future__ import annotations

import unicodedata

from typing import Any

from sqlalchemy import event, func
from sqlalchemy.engine import Engine
from sqlalchemy.sql import ColumnElement


def strip_accents(value: str | None) -> str | None:
    if value is None:
        return None
    return "".join(
        c
        for c in unicodedata.normalize("NFD", value)
        if unicodedata.category(c) != "Mn"
    )


def unaccent_ilike(column: Any, value: str) -> ColumnElement[bool]:
    """Build an accent-insensitive, case-insensitive ILIKE clause.

    Compares ``unaccent(column)`` against ``unaccent('%value%')`` so a search
    for "reconstruccion" matches stored "Reconstrucción" and vice versa.
    """
    pattern = f"%{value}%"
    return func.unaccent(column).ilike(func.unaccent(pattern))


def register_sqlite_unaccent(engine: Engine) -> None:
    """Register a Python ``unaccent`` UDF on SQLite connections."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        if hasattr(dbapi_conn, "create_function"):
            dbapi_conn.create_function("unaccent", 1, strip_accents, deterministic=True)
