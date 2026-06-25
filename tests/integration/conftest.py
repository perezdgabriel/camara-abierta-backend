import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.database import Base, build_engine, get_db
from app.main import create_app


def _load_test_database_url() -> str:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL is required when running integration tests."
        )

    runtime_database_url = os.getenv("DATABASE_URL")
    if runtime_database_url and test_database_url == runtime_database_url:
        raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL.")

    parsed_url: URL = make_url(test_database_url)
    if parsed_url.get_backend_name() != "postgresql":
        raise RuntimeError("TEST_DATABASE_URL must use a PostgreSQL database.")

    database_name = parsed_url.database or ""
    if not database_name.endswith("_test"):
        raise RuntimeError(
            "TEST_DATABASE_URL must target a disposable database whose name ends "
            "with '_test'."
        )

    return test_database_url


@pytest.fixture(scope="session")
def integration_database_url(pytestconfig: pytest.Config) -> str:
    if not pytestconfig.getoption("--integration"):
        pytest.skip("integration tests require --integration")
    return _load_test_database_url()


@pytest.fixture(scope="session")
def integration_engine(integration_database_url: str) -> Iterator[Engine]:
    engine = build_engine(integration_database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS unaccent"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def db_connection(integration_engine: Engine) -> Iterator[Connection]:
    connection = integration_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session_factory(
    db_connection: Connection,
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_connection,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def api_app(db_session_factory: sessionmaker[Session]) -> Iterator[FastAPI]:
    app = create_app(include_admin=False)

    def override_get_db() -> Iterator[Session]:
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app) as test_client:
        yield test_client
