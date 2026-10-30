"""One-shot migration runner (the `migrate` compose service). Safe to run any number of times."""

import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.config import load_settings
from app.db.engine import make_engine
from app.logging_config import configure_logging

log = logging.getLogger("filecanopy.migrate")

# Arbitrary constant; serializes concurrent migrators (e.g. two instances starting together).
MIGRATION_LOCK_ID = 0x46434D47  # "FCMG"
API_ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    return cfg


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no migrations found")
    return head


def upgrade() -> None:
    engine = make_engine(load_settings())
    cfg = alembic_config()
    try:
        with engine.connect() as lock_conn:
            lock_conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
            try:
                with engine.begin() as conn:
                    cfg.attributes["connection"] = conn
                    command.upgrade(cfg, "head")
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
                lock_conn.commit()
    finally:
        engine.dispose()


def main() -> int:
    configure_logging()
    try:
        upgrade()
    except Exception as exc:
        # Type only: exception text can embed the database URL.
        log.error("migration failed: %s", type(exc).__name__)
        return 1
    log.info("migrations applied (head=%s)", head_revision())
    return 0


if __name__ == "__main__":
    sys.exit(main())
