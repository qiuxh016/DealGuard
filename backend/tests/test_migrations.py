from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.database import Base
from app.db import models  # noqa: F401


def test_legacy_sqlite_migrates_without_losing_rows(tmp_path, monkeypatch):
    database = tmp_path / "p0.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE customers (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE products (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, currency VARCHAR NOT NULL, unit_price NUMERIC(12,2) NOT NULL, stock INTEGER NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE demo_actors (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE policies (id VARCHAR PRIMARY KEY, policy_id VARCHAR NOT NULL, version VARCHAR NOT NULL, title VARCHAR NOT NULL, status VARCHAR NOT NULL, customer_tier VARCHAR NOT NULL, max_discount_pct NUMERIC(5,2) NOT NULL, exception_approval_allowed BOOLEAN NOT NULL, effective_from DATETIME NOT NULL, effective_to DATETIME, source TEXT NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE quote_drafts (id VARCHAR PRIMARY KEY, owner_actor_id VARCHAR NOT NULL REFERENCES demo_actors(id), customer_id VARCHAR NOT NULL REFERENCES customers(id), product_id VARCHAR NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL, unit_price NUMERIC(12,2) NOT NULL, discount_pct NUMERIC(5,2) NOT NULL, subtotal NUMERIC(14,2) NOT NULL, discount_amount NUMERIC(14,2) NOT NULL, total NUMERIC(14,2) NOT NULL, policy_id VARCHAR NOT NULL, policy_version VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL)")
        connection.exec_driver_sql("CREATE TABLE runs (id VARCHAR PRIMARY KEY, actor_id VARCHAR NOT NULL, inquiry TEXT NOT NULL, status VARCHAR NOT NULL, execution_mode VARCHAR NOT NULL, result JSON NOT NULL, created_at DATETIME NOT NULL)")
        connection.exec_driver_sql("INSERT INTO policies VALUES ('p1','DISCOUNT-01','v1','v1','active','VIP',10,0,'2026-01-01',NULL,'source-v1')")
        connection.exec_driver_sql("INSERT INTO customers VALUES ('C-001')")
        connection.exec_driver_sql("INSERT INTO products VALUES ('P-100','Product A','USD',100.00,2000)")
        connection.exec_driver_sql("INSERT INTO demo_actors VALUES ('sales_a')")
        connection.exec_driver_sql("INSERT INTO runs VALUES ('r1','sales_a','quote','completed','demo','{\"trace\":[{\"step\":\"legacy\"}]}','2026-11-01')")
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('0001_baseline')")
    monkeypatch.setenv("DEALGUARD_MIGRATION_URL", f"sqlite:///{database.as_posix()}")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0013_password_reset_tokens")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO quote_drafts (id,owner_actor_id,customer_id,product_id,quantity,unit_price,discount_pct,subtotal,discount_amount,total,policy_id,policy_version,status,created_at,tenant_id) VALUES ('q1','sales_a','C-001','P-100',10,100,5,1000,50,950,'DISCOUNT-01','v1','DRAFT','2026-11-03','harborlight-demo')"))
        connection.execute(text("INSERT INTO approval_requests (id,run_id,applicant_actor_id,customer_id,product_id,quantity,discount_pct,policy_id,policy_version,state,created_at,tenant_id) VALUES ('a1','r1','sales_a','C-001','P-100',10,7,'DISCOUNT-01','v1','PENDING','2026-11-03','harborlight-demo')"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT source FROM policies WHERE id='p1'")) == "source-v1"
        assert connection.scalar(text("SELECT inquiry FROM runs WHERE id='r1'")) == "quote"
        assert "legacy" in connection.scalar(text("SELECT result FROM runs WHERE id='r1'"))
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0014_product_sku"
        product = connection.execute(text("SELECT id,sku FROM products WHERE sku='P-100'")).one()
        assert product.id != "P-100"
        assert connection.scalar(text("SELECT product_id FROM quote_drafts WHERE id='q1'")) == "P-100"
        assert connection.scalar(text("SELECT product_key FROM quote_drafts WHERE id='q1'")) == product.id
        assert connection.scalar(text("SELECT product_id FROM approval_requests WHERE id='a1'")) == "P-100"
        assert connection.scalar(text("SELECT product_key FROM approval_requests WHERE id='a1'")) == product.id
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.scalar(text("SELECT action_type FROM policies WHERE id='p1'")) == "quote_discount"
        assert next(column for column in inspect(connection).get_columns("policies") if column["name"] == "max_discount_pct")["nullable"] is True
        assert "action_type" in {column["name"] for column in inspect(connection).get_columns("policy_drafts")}
        assert connection.scalar(text("SELECT attempt FROM runs WHERE id='r1'")) == 1
        assert connection.scalar(text("SELECT lease_expires_at FROM runs WHERE id='r1'")) is None
        assert connection.scalar(text("SELECT policy_scope FROM policies WHERE id='p1'")) == "general"
    tables = set(inspect(engine).get_table_names())
    assert {"approval_requests", "policy_drafts", "policy_audit_events", "workspace_invitations", "password_reset_tokens"}.issubset(tables)
    assert {column["name"] for column in inspect(engine).get_columns("runs")} >= {
        "duration_ms", "input_tokens", "output_tokens", "idempotency_key", "request_hash", "attempt", "lease_expires_at",
    }
    indexes = inspect(engine).get_indexes("runs")
    constraints = inspect(engine).get_unique_constraints("runs")
    assert not any(index["name"] == "uq_runs_actor_idempotency_key" for index in indexes)
    assert any(item["name"] == "uq_runs_tenant_actor_idempotency_key" for item in constraints)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tenants (id,name) VALUES ('tenant-two','Second workspace')"))
        connection.execute(text("INSERT INTO products (id,sku,name,currency,unit_price,stock,tenant_id) VALUES ('uuid-two','P-100','Product B','USD',20,25,'tenant-two')"))
    with pytest.raises(RuntimeError, match="exists in multiple workspaces"):
        command.downgrade(config, "0013_password_reset_tokens")
    with engine.begin() as connection:
        assert len(connection.execute(text("SELECT id FROM products WHERE sku='P-100'")).all()) == 2
        connection.execute(text("DELETE FROM products WHERE id='uuid-two'"))
        connection.execute(text("DELETE FROM tenants WHERE id='tenant-two'"))
    command.downgrade(config, "0013_password_reset_tokens")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT id FROM products WHERE id='P-100'")) == "P-100"
        assert connection.scalar(text("SELECT product_id FROM quote_drafts WHERE id='q1'")) == "P-100"
        assert "product_key" not in {column["name"] for column in inspect(connection).get_columns("approval_requests")}
    command.upgrade(config, "head")
    command.downgrade(config, "0004_run_recovery")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004_run_recovery"
        assert "tenant_id" not in {column["name"] for column in inspect(connection).get_columns("runs")}
    assert "tenants" not in inspect(engine).get_table_names()
    assert any(index["name"] == "uq_runs_actor_idempotency_key" and index["unique"] for index in inspect(engine).get_indexes("runs"))
    command.upgrade(config, "head")
    engine.dispose()


def test_fresh_metadata_database_can_be_stamped_and_upgraded(tmp_path):
    database = tmp_path / "fresh.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.stamp(config, "0001_baseline")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0014_product_sku"
        assert "email" in {column["name"] for column in inspect(connection).get_columns("auth_accounts")}
        assert {"policy_audit_events", "workspace_invitations", "password_reset_tokens"}.issubset(inspect(connection).get_table_names())
    engine.dispose()
