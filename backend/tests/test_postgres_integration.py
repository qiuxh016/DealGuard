from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db.database import engine
from app.db.migrations import upgrade_database
from app.main import app


pytestmark = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL integration check requires DATABASE_URL to use PostgreSQL")


def test_postgres_registration_and_invitation_respect_foreign_keys():
    suffix = uuid4().hex
    with TestClient(app) as client:
        registered = client.post("/api/v1/auth/register", json={
            "organization_name": "Registration FK Check",
            "display_name": "Workspace Admin",
            "email": f"admin-{suffix}@example.test",
            "password": "a-secure-password-123",
        })
        assert registered.status_code == 201, registered.text
        admin = registered.json()

        invitation = client.post("/api/v1/auth/invitations", json={
            "email": f"manager-{suffix}@example.test",
            "role": "manager",
        })
        assert invitation.status_code == 201, invitation.text

        accepted = client.post("/api/v1/auth/accept-invitation", json={
            "token": invitation.json()["token"],
            "display_name": "Workspace Manager",
            "password": "another-secure-password-456",
        })
        assert accepted.status_code == 201, accepted.text
        manager = accepted.json()

    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM tenants WHERE id = :id"), {"id": admin["tenant_id"]}).scalar_one()
        assert connection.execute(text("SELECT id FROM demo_actors WHERE id = :id"), {"id": admin["actor_id"]}).scalar_one()
        assert connection.execute(text("SELECT actor_id FROM auth_accounts WHERE actor_id = :id"), {"id": admin["actor_id"]}).scalar_one()
        assert connection.execute(text("SELECT id FROM demo_actors WHERE id = :id"), {"id": manager["actor_id"]}).scalar_one()
        assert connection.execute(text("SELECT actor_id FROM auth_accounts WHERE actor_id = :id"), {"id": manager["actor_id"]}).scalar_one()


def test_postgres_quote_write_and_demo_reset():
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/runs",
            headers={"X-Demo-Actor": "sales_a", "Idempotency-Key": "postgres-integration-quote"},
            json={"inquiry": "I need 1,000 units of Product A at an 8% discount."},
        )
        assert result.status_code == 200
        assert result.json()["decision"]["decision"] == "ALLOW"
        assert result.json()["quote"]["breakdown"]["total"] == "92000.00"

        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM quote_drafts WHERE tenant_id = 'harborlight-demo'")).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM runs WHERE idempotency_key = 'postgres-integration-quote'")).scalar_one() == 1

        reset = client.post("/api/v1/demo/reset", headers={"X-Demo-Actor": "admin"})
        assert reset.status_code == 200
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM quote_drafts WHERE tenant_id = 'harborlight-demo'")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM runs WHERE tenant_id = 'harborlight-demo'")).scalar_one() == 0


def test_postgres_tenant_migration_round_trip_preserves_rows():
    with TestClient(app):
        pass
    tables = ("customers", "demo_actors", "products", "policies")
    with engine.connect() as connection:
        before = {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in tables}

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%"))
    command.downgrade(config, "0004_run_recovery")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004_run_recovery"
        assert "tenant_id" not in {column["name"] for column in inspect(connection).get_columns("customers")}

    upgrade_database()
    with engine.connect() as connection:
        after = {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() for table in tables}
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0014_product_sku"
        assert connection.execute(text("SELECT count(*) FROM policies WHERE action_type = 'quote_discount'")).scalar_one() == after["policies"]
        assert after == before
        assert connection.execute(text("SELECT count(*) FROM customers WHERE tenant_id = 'harborlight-demo'")).scalar_one() == before["customers"]
