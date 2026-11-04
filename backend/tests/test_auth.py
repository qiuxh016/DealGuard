from app.core.config import settings
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuthAccount, AuthSession, Policy, Tenant
from app.services.actors import hash_password, issue_session


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


def test_registration_creates_isolated_workspace_admin_and_session(client):
    response = client.post("/api/v1/auth/register", json={
        "organization_name": "Northwind Trading", "display_name": "Alex Morgan",
        "email": "Alex@Northwind.example", "password": "a-secure-password-123",
    })
    assert response.status_code == 201, response.text
    actor = response.json()
    assert actor["display_name"] == "Alex Morgan"
    assert actor["role"] == "admin"
    assert actor["tenant_id"] != "harborlight-demo"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.get("/api/v1/auth/me").json()["actor_id"] == actor["actor_id"]
    with client.app.state.session_factory() as db:
        account = db.get(AuthAccount, actor["actor_id"])
        assert account.email == "alex@northwind.example"
        assert account.password_hash != "a-secure-password-123"
        assert db.get(Tenant, actor["tenant_id"]).name == "Northwind Trading"
        assert db.scalar(select(Policy).where(Policy.tenant_id == actor["tenant_id"])) is None


def test_profile_update_and_password_change_rotate_sessions(client):
    registered = client.post("/api/v1/auth/register", json={
        "organization_name": "Northwind Trading", "display_name": "Alex Morgan",
        "email": "alex@northwind.example", "password": "a-secure-password-123",
    })
    assert registered.status_code == 201
    profile = client.get("/api/v1/auth/me")
    assert profile.json()["email"] == "alex@northwind.example"
    assert profile.json()["workspace_name"] == "Northwind Trading"

    renamed = client.patch("/api/v1/auth/me", json={"display_name": "Alex Morgan Jr."})
    assert renamed.status_code == 200
    assert client.get("/api/v1/auth/me").json()["display_name"] == "Alex Morgan Jr."

    wrong_password = client.post("/api/v1/auth/change-password", json={
        "current_password": "incorrect-password", "new_password": "another-secure-password-456",
    })
    assert wrong_password.status_code == 400
    assert wrong_password.json()["detail"]["code"] == "CURRENT_PASSWORD_INVALID"

    with client.app.state.session_factory() as db:
        old_token, _ = issue_session(db, registered.json()["actor_id"])
    changed = client.post("/api/v1/auth/change-password", json={
        "current_password": "a-secure-password-123", "new_password": "another-secure-password-456",
    })
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/auth/me").status_code == 200
    with TestClient(client.app, cookies={"dealguard_session": old_token}) as old_session_client:
        assert old_session_client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "alex@northwind.example", "password": "a-secure-password-123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "alex@northwind.example", "password": "another-secure-password-456"}).status_code == 200


def test_registration_rejects_duplicate_email_and_client_role(client):
    body = {"organization_name": "Northwind", "display_name": "Alex Morgan", "email": "alex@example.com", "password": "a-secure-password-123"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    duplicate = client.post("/api/v1/auth/register", json={**body, "email": "ALEX@example.com"})
    assert duplicate.status_code == 409
    role_override = client.post("/api/v1/auth/register", json={**body, "email": "new@example.com", "role": "admin"})
    assert role_override.status_code == 422


def test_registration_requires_strong_password(client):
    response = client.post("/api/v1/auth/register", json={
        "organization_name": "Northwind", "display_name": "Alex Morgan",
        "email": "alex@example.com", "password": "short",
    })
    assert response.status_code == 422


def test_expired_or_disabled_account_session_is_rejected(client):
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(AuthAccount(actor_id="sales_a", password_hash=hash_password("a-long-demo-password")))
        db.commit()
    assert client.post("/api/v1/auth/login", json={"actor_id": "sales_a", "password": "a-long-demo-password"}).status_code == 200
    with factory() as db:
        session = db.query(AuthSession).one()
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401

    assert client.post("/api/v1/auth/login", json={"actor_id": "sales_a", "password": "a-long-demo-password"}).status_code == 200
    with factory() as db:
        db.get(AuthAccount, "sales_a").is_active = False
        db.commit()
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "SESSION_INVALID"


def test_login_failure_does_not_disclose_unknown_account(client):
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(AuthAccount(actor_id="sales_a", password_hash=hash_password("a-long-demo-password")))
        db.commit()
    unknown = client.post("/api/v1/auth/login", json={"actor_id": "missing", "password": "incorrect"})
    wrong_password = client.post("/api/v1/auth/login", json={"actor_id": "sales_a", "password": "incorrect"})
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json() == wrong_password.json()


def test_login_rate_limit_is_configurable_per_client_ip(client, monkeypatch):
    from app.api.routes import auth as auth_route

    monkeypatch.setattr(auth_route, "_login_failures", {})
    monkeypatch.setattr(settings, "auth_login_max_attempts", 3)
    monkeypatch.setattr(settings, "auth_login_window_seconds", 60)
    for index in range(3):
        response = client.post("/api/v1/auth/login", json={"actor_id": f"missing-{index}", "password": "incorrect"})
        assert response.status_code == 401
    limited = client.post("/api/v1/auth/login", json={"actor_id": "another-missing", "password": "incorrect"})
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "LOGIN_RATE_LIMITED"
    assert int(limited.headers["Retry-After"]) > 0


def test_production_rejects_demo_actor_header(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get("/api/v1/policies", headers={"X-Demo-Actor": "admin"}).status_code == 401


def test_demo_actor_header_requires_explicit_opt_in(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_demo_auth", False)
    response = client.get("/api/v1/policies", headers={"X-Demo-Actor": "sales_a"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


def test_policy_list_is_scoped_to_authenticated_tenant(client):
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(Tenant(id="other-tenant", name="Other"))
        db.flush()
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
