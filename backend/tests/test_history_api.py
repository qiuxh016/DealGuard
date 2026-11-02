from app.db.models import PolicyAuditEvent, Tenant


def _run(client, actor: str, key: str, inquiry: str):
    response = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": actor, "Idempotency-Key": key},
        json={"inquiry": inquiry},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_run_history_filters_and_sales_scope(client):
    allow = _run(client, "sales_a", "history-allow", "Product A 100 units 5% discount")
    deny = _run(client, "sales_b", "history-deny", "Product A 100 units 8% discount")

    own = client.get("/api/v1/runs", headers={"X-Demo-Actor": "sales_a"})
    assert own.status_code == 200
    assert own.json()["total"] == 1
    assert own.json()["items"][0]["run_id"] == allow["run_id"]
    assert "inquiry" not in own.json()["items"][0]

    filtered = client.get("/api/v1/runs?decision=DENY", headers={"X-Demo-Actor": "admin"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["run_id"] == deny["run_id"]

    policy_filtered = client.get("/api/v1/runs?policy_version=v1", headers={"X-Demo-Actor": "admin"})
    assert policy_filtered.status_code == 200
    assert policy_filtered.json()["total"] == 1
    assert policy_filtered.json()["items"][0]["run_id"] == allow["run_id"]

    invalid_range = client.get(
        "/api/v1/runs?created_from=2026-01-02T00:00:00Z&created_to=2026-01-01T00:00:00Z",
        headers={"X-Demo-Actor": "admin"},
    )
    assert invalid_range.status_code == 422


def test_policy_audit_history_is_admin_only_and_tenant_scoped(client):
    with client.app.state.session_factory() as db:
        db.add(Tenant(id="history-other-tenant", name="History other tenant"))
        db.add_all([
            PolicyAuditEvent(tenant_id="harborlight-demo", actor_id="admin", action="policy_draft_updated",
                             outcome="SUCCESS", policy_id="DISCOUNT-02", version="v2"),
            PolicyAuditEvent(tenant_id="history-other-tenant", actor_id="admin", action="policy_draft_updated",
                             outcome="SUCCESS", policy_id="SECRET", version="v1"),
        ])
        db.commit()

    denied = client.get("/api/v1/audit", headers={"X-Demo-Actor": "manager"})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "AUDIT_ROLE_REQUIRED"

    response = client.get("/api/v1/audit?action=policy_draft_updated", headers={"X-Demo-Actor": "admin"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["policy_id"] == "DISCOUNT-02"
    assert all(item["policy_id"] != "SECRET" for item in response.json()["items"])


def test_evaluation_report_is_role_protected_and_labeled_as_test_adapter(client):
    denied = client.get("/api/v1/evaluation", headers={"X-Demo-Actor": "sales_a"})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "EVALUATION_ROLE_REQUIRED"

    response = client.get("/api/v1/evaluation?split=holdout", headers={"X-Demo-Actor": "manager"})
    assert response.status_code == 200
    report = response.json()
    assert report["split"] == "holdout"
    assert report["offline_test_adapter_only"] is True
    assert report["real_model_evaluation"] is False
    assert report["methods"]["rrf"]["cases"] == 15
    assert "failed_case_ids" in report["methods"]["bm25"]
