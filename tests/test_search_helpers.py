"""Smoke tests for accent-insensitive search helpers."""

from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.search import register_sqlite_unaccent, strip_accents, unaccent_ilike


class _Base(DeclarativeBase):
    pass


class _Item(_Base):
    __tablename__ = "search_items"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)


def _build_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    register_sqlite_unaccent(engine)
    _Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            _Item(id=1, title="Reconstrucción nacional"),
            _Item(id=2, title="Educación pública"),
            _Item(id=3, title="Salud"),
        ]
    )
    session.commit()
    return session


def test_strip_accents_handles_spanish_diacritics():
    assert strip_accents("Reconstrucción") == "Reconstruccion"
    assert strip_accents("piña") == "pina"
    assert strip_accents(None) is None


def test_unaccent_ilike_matches_without_tilde():
    session = _build_session()
    rows = session.scalars(
        select(_Item).where(unaccent_ilike(_Item.title, "reconstruccion"))
    ).all()
    assert [row.id for row in rows] == [1]


def test_unaccent_ilike_matches_with_tilde():
    session = _build_session()
    rows = session.scalars(
        select(_Item).where(unaccent_ilike(_Item.title, "educación"))
    ).all()
    assert [row.id for row in rows] == [2]
