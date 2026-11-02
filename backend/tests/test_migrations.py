from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_legacy_sqlite_migrates_without_losing_rows(tmp_path, monkeypatch):
    database = tmp_path / "p0.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE customers (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE products (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE demo_actors (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE policies (id VARCHAR PRIMARY KEY, policy_id VARCHAR NOT NULL, version VARCHAR NOT NULL, title VARCHAR NOT NULL, status VARCHAR NOT NULL, customer_tier VARCHAR NOT NULL, max_discount_pct NUMERIC(5,2) NOT NULL, exception_approval_allowed BOOLEAN NOT NULL, effective_from DATETIME NOT NULL, effective_to DATETIME, source TEXT NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE quote_drafts (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE runs (id VARCHAR PRIMARY KEY, actor_id VARCHAR NOT NULL, inquiry TEXT NOT NULL, status VARCHAR NOT NULL, execution_mode VARCHAR NOT NULL, result JSON NOT NULL, created_at DATETIME NOT NULL)")
        connection.exec_driver_sql("INSERT INTO policies VALUES ('p1','DISCOUNT-01','v1','v1','active','VIP',10,0,'2026-01-01',NULL,'source-v1')")
        connection.exec_driver_sql("INSERT INTO runs VALUES ('r1','sales_a','quote','completed','demo','{\"trace\":[{\"step\":\"legacy\"}]}','2026-11-01')")
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('0001_baseline')")
    monkeypatch.setenv("DEALGUARD_MIGRATION_URL", f"sqlite:///{database.as_posix()}")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT source FROM policies WHERE id='p1'")) == "source-v1"
        assert connection.scalar(text("SELECT inquiry FROM runs WHERE id='r1'")) == "quote"
        assert "legacy" in connection.scalar(text("SELECT result FROM runs WHERE id='r1'"))
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0006_drop_legacy_idempotency_index"
        assert connection.scalar(text("SELECT attempt FROM runs WHERE id='r1'")) == 1
        assert connection.scalar(text("SELECT lease_expires_at FROM runs WHERE id='r1'")) is None
    tables = set(inspect(engine).get_table_names())
    assert {"approval_requests", "policy_drafts"}.issubset(tables)
    assert {column["name"] for column in inspect(engine).get_columns("runs")} >= {
        "duration_ms", "input_tokens", "output_tokens", "idempotency_key", "request_hash", "attempt", "lease_expires_at",
    }
    indexes = inspect(engine).get_indexes("runs")
    constraints = inspect(engine).get_unique_constraints("runs")
    assert not any(index["name"] == "uq_runs_actor_idempotency_key" for index in indexes)
    assert any(item["name"] == "uq_runs_tenant_actor_idempotency_key" for item in constraints)
    command.downgrade(config, "0004_run_recovery")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004_run_recovery"
        assert "tenant_id" not in {column["name"] for column in inspect(connection).get_columns("runs")}
    assert "tenants" not in inspect(engine).get_table_names()
    assert any(index["name"] == "uq_runs_actor_idempotency_key" and index["unique"] for index in inspect(engine).get_indexes("runs"))
    command.upgrade(config, "head")
    engine.dispose()
