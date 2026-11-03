import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.db.database import enable_sqlite_foreign_keys


def test_sqlite_runtime_engine_enforces_foreign_keys():
    engine = create_engine("sqlite://")
    enable_sqlite_foreign_keys(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))"))
        with engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO child(parent_id) VALUES (404)"))
    finally:
        engine.dispose()
