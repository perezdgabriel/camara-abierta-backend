from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings
from app.core.search import register_sqlite_unaccent

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def build_engine(database_url: str) -> Engine:
    # Postgres (deployed): a small, recycled pool sized for Lambda — a warm
    # container reuses one connection, pre_ping revalidates after a freeze, and
    # recycle avoids RDS-side idle drops, all while staying well under
    # max_connections. The pool_size/max_overflow kwargs are invalid for the
    # in-memory SQLite test pool, so only Postgres gets them.
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith("postgresql"):
        kwargs.update(pool_size=1, max_overflow=2, pool_recycle=1800)
    engine = create_engine(database_url, **kwargs)
    if engine.dialect.name == "sqlite":
        register_sqlite_unaccent(engine)
    return engine


def build_sessionmaker(bind: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=bind, autocommit=False, autoflush=False)


engine = build_engine(settings.database_url)
SessionLocal = build_sessionmaker(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
