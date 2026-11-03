from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select

from app.db.models import DemoActor, Policy


def test_registered_workspace_admin_can_setup_data_and_run_quote(client):
    registered = client.post("/api/v1/auth/register", json={
        "organization_name": "Northwind Trading", "display_name": "Alex Morgan",
        "email": "owner@northwind.example", "password": "a-secure-password-123",
    })
    assert registered.status_code == 201
    tenant_id = registered.json()["tenant_id"]

    customer = client.post("/api/v1/customers", json={"name": "Contoso Retail", "tier": "VIP"})
    assert customer.status_code == 201
    product = client.post("/api/v1/products", json={
        "id": "P-900", "name": "Industrial Pump", "currency": "USD", "unit_price": "12.50", "stock": 100,
    })
    assert product.status_code == 201
    assert [row["id"] for row in client.get("/api/v1/customers").json()] == [customer.json()["id"]]
    assert [row["id"] for row in client.get("/api/v1/products").json()] == ["P-900"]
    assert client.get("/api/v1/auth/me").json()["actor_id"] == registered.json()["actor_id"]

    with client.app.state.session_factory() as db:
        actor = db.get(DemoActor, registered.json()["actor_id"])
        db.add(Policy(
            policy_id="STARTER-DISCOUNT", version="v1", title="Starter discount", action_type="quote_discount",
            policy_scope="general", status="active", customer_tier="VIP", max_discount_pct=Decimal("5"),
            exception_approval_allowed=False, effective_from=datetime.now(timezone.utc), source="VIP buyers may receive a discount up to 5%.",
            tenant_id=actor.tenant_id,
        ))
        db.commit()
    run = client.post("/api/v1/runs", json={
        "inquiry": "Please quote P-900 10 units at 5%.", "customer_id": customer.json()["id"],
    })
    assert run.status_code == 200, run.text
    assert run.json()["decision"]["decision"] == "ALLOW", {"intent": run.json()["intent"], "load_details": run.json()["trace"][1]["details"]}
    assert run.json()["quote"]["breakdown"]["total"] == "118.75"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Policy).where(Policy.tenant_id == tenant_id)) is not None


def test_registered_workspace_data_is_tenant_isolated(client):
    registered = client.post("/api/v1/auth/register", json={
        "organization_name": "Northwind Trading", "display_name": "Alex Morgan",
        "email": "owner@northwind.example", "password": "a-secure-password-123",
    })
    assert registered.status_code == 201
    assert client.get("/api/v1/customers").json() == []
    assert client.get("/api/v1/products").json() == []
