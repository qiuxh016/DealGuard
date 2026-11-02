from app.core.config import settings
from app.db.models import AuthAccount, Policy, Tenant
from app.services.actors import hash_password


def test_login_cookie_session_and_logout(client, monkeypatch):
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(AuthAccount(actor_id="sales_a", password_hash=hash_password("a-long-demo-password")))
        db.commit()
    logged_in = client.post("/api/v1/auth/login", json={"actor_id": "sales_a", "password": "a-long-demo-password"})
    assert logged_in.status_code == 200
    assert logged_in.json()["role"] == "sales"
    assert "httponly" in logged_in.headers["set-cookie"].lower()
    assert client.get("/api/v1/auth/me").json()["actor_id"] == "sales_a"
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get("/api/v1/policies", headers={"X-Demo-Actor": "admin"}).status_code == 200
    assert client.post("/api/v1/policies/not-a-policy/activate", headers={"X-Demo-Actor": "admin"}).status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_production_rejects_demo_actor_header(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get("/api/v1/policies", headers={"X-Demo-Actor": "admin"}).status_code == 401


def test_policy_list_is_scoped_to_authenticated_tenant(client):
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(Tenant(id="other-tenant", name="Other"))
        source = db.query(Policy).filter_by(version="v1").one()
        db.add(Policy(policy_id="OTHER", version="v1", title="Other tenant policy", status="active",
                      customer_tier=source.customer_tier, max_discount_pct=source.max_discount_pct,
                      exception_approval_allowed=False, effective_from=source.effective_from, source="other tenant source",
                      tenant_id="other-tenant"))
        db.commit()
    response = client.get("/api/v1/policies", headers={"X-Demo-Actor": "sales_a"})
    assert response.status_code == 200
    assert all(row["policy_id"] != "OTHER" for row in response.json())
    assert client.post("/api/v1/demo/reset", headers={"X-Demo-Actor": "admin"}).status_code == 200
    with factory() as db:
        assert db.query(Policy).filter_by(tenant_id="other-tenant", policy_id="OTHER").count() == 1


def test_demo_reset_preserves_accounts_and_builtin_actors(client):
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(AuthAccount(actor_id="admin", password_hash=hash_password("another-long-password")))
        db.commit()
    reset = client.post("/api/v1/demo/reset", headers={"X-Demo-Actor": "admin"})
    assert reset.status_code == 200
    with factory() as db:
        assert db.get(AuthAccount, "admin") is not None
