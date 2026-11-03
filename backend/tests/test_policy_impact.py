from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import ApprovalRequest, PolicyDraft, QuoteDraft, RunRecord


def _create_base_run(client):
    response = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "impact-base-run"},
        json={"inquiry": "Please quote 100 units of Product A at 8% discount."},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_candidate(client):
    with client.app.state.session_factory() as db:
        draft = PolicyDraft(
            policy_id="DISCOUNT-01", version="impact-v4", title="VIP discount maximum 5%",
            action_type="quote_discount", policy_scope="general", customer_tier="VIP",
            max_discount_pct=Decimal("5"), exception_approval_allowed=False,
            source="# VIP Discount Policy\n\nVerified VIP customers may receive a maximum discount of 5%.",
            status="draft", uploaded_by="admin", tenant_id="harborlight-demo",
            created_at=datetime.now(timezone.utc),
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft.id


def test_frozen_policy_impact_replays_saved_guard_snapshot_without_writes(client):
    run = _create_base_run(client)
    draft_id = _create_candidate(client)
    with client.app.state.session_factory() as db:
        run_record = db.get(RunRecord, run["run_id"])
        assert run_record.result["_impact_snapshot"]["lines"][0]["product"]["stock"] == 2000
        quote_count = db.scalar(select(func.count()).select_from(QuoteDraft))
        approval_count = db.scalar(select(func.count()).select_from(ApprovalRequest))

    response = client.post("/api/v1/policy-impact/simulate", headers={"X-Demo-Actor": "admin"}, json={
        "policy_draft_id": draft_id, "run_ids": [run["run_id"]], "mode": "frozen_intent_replay",
    })

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["replayed_cases"] == 1
    assert report["changed_cases"] == 1
    assert report["transitions"] == {"ALLOW->DENY": 1}
    assert report["cases"][0]["baseline_reason_code"] == "WITHIN_POLICY"
    assert report["cases"][0]["candidate_reason_code"] == "DISCOUNT_LIMIT_EXCEEDED"
    assert report["writes_created"] is False
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == quote_count
        assert db.scalar(select(func.count()).select_from(ApprovalRequest)) == approval_count


def test_end_to_end_policy_impact_rolls_back_simulated_quote_writes(client):
    run = _create_base_run(client)
    draft_id = _create_candidate(client)
    with client.app.state.session_factory() as db:
        quote_count = db.scalar(select(func.count()).select_from(QuoteDraft))
        approval_count = db.scalar(select(func.count()).select_from(ApprovalRequest))

    response = client.post("/api/v1/policy-impact/simulate", headers={"X-Demo-Actor": "admin"}, json={
        "policy_draft_id": draft_id, "run_ids": [run["run_id"]], "mode": "end_to_end_replay",
    })

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["replayed_cases"] == 1
    assert report["changed_cases"] == 1
    assert report["writes_created"] is False
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == quote_count
        assert db.scalar(select(func.count()).select_from(ApprovalRequest)) == approval_count


def test_policy_impact_requires_admin(client):
    response = client.post("/api/v1/policy-impact/simulate", headers={"X-Demo-Actor": "manager"}, json={
        "policy_draft_id": "missing", "mode": "frozen_intent_replay",
    })
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ADMIN_REQUIRED"
