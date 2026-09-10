"""Stage 3: verify the Alembic migration itself applies cleanly to a fresh DB.

Runs against a scratch SQLite file DB (Alembic doesn't handle :memory: well since
each connection gets its own DB) to prove `alembic upgrade head` works end-to-end,
independent of the psycopg/Postgres driver which isn't available in this test env.
"""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_creates_all_tables(tmp_path):
    db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_file}"

    os.environ["DATABASE_URL"] = db_url
    from app.config import get_settings
    get_settings.cache_clear()

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert {"jobs", "application_events", "processed_messages", "alembic_version"} <= tables

    del os.environ["DATABASE_URL"]
    get_settings.cache_clear()
