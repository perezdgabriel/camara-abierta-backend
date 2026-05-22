import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

api_router = importlib.import_module("app.api.router").router
get_db = importlib.import_module("app.core.database").get_db


@pytest.fixture
def fake_db() -> object:
    return object()


@pytest.fixture
def api_app(fake_db: object) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router)

    def override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
def client(api_app: FastAPI) -> TestClient:
    with TestClient(api_app) as test_client:
        yield test_client
