"""Shared fixtures for API tests: a fresh SQLite-backed FastAPI TestClient per test."""
import os

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.config import get_settings


@pytest.fixture
def client(tmp_path):
    db_file = tmp_path / "api_test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    get_settings.cache_clear()

    # Reset app.db's module-level engine/session-factory singletons so they
    # pick up the sqlite URL instead of whatever was cached before.
    db_module._engine = None
    db_module._SessionLocal = None

    from app.db import Base

    engine = db_module.get_engine()
    Base.metadata.create_all(engine)

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as test_client:
        yield test_client

    db_module._engine = None
    db_module._SessionLocal = None
    del os.environ["DATABASE_URL"]
    get_settings.cache_clear()


@pytest.fixture
def session_factory(client):
    """The same session factory the API under test is using — for tests to
    seed/inspect rows directly."""
    return db_module.get_session_factory()
