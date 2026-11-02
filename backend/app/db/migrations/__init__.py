from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db.database import Base, engine
from app.db import models  # noqa: F401


def upgrade_database() -> None:
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%"))
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables and {"policies", "runs", "quote_drafts"}.issubset(tables):
        command.stamp(config, "0001_baseline")
    elif "alembic_version" not in tables:
        Base.metadata.create_all(bind=engine)
        command.stamp(config, "0001_baseline")
        command.upgrade(config, "head")
        return
    command.upgrade(config, "head")
