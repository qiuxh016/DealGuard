from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import Customer, DemoActor, Policy, Product, QuoteDraft
from app.guards.business_rules import evaluate_quote
from app.schemas.deal import ActionProposal


def _upload_policy(client, policy_id: str, version: str):
    return client.post(
        "/api/v1/policy-drafts",
        headers={"X-Demo-Actor": "admin"},
        data={"policy_id": policy_id, "version": version},
        files={"file": ("reference.md", "This document is informational reference text.", "text/markdown")},
    )


def _confirm_reference_only(client, draft_id: str):
    return client.put(
        f"/api/v1/policy-drafts/{draft_id}",
        headers={"X-Demo-Actor": "admin"},
        json={
            "title": "Shipping terms reference",
            "action_type": "reference_only",
            "customer_tier": "VIP",
            "max_discount_pct": None,
            "review_max_discount_pct": None,
            "exception_approval_allowed": False,
        },
    )


def test_active_reference_only_policy_is_excluded_from_discount_quotes(client):
    draft = _upload_policy(client, "SHIPPING-REF", "v1").json()
    assert draft["action_type"] is None
    assert client.post(
        f"/api/v1/policy-drafts/{draft['id']}/activate",
        headers={"X-Demo-Actor": "admin"},
    ).json()["detail"]["code"] == "POLICY_ACTION_REQUIRED"
    confirmed = _confirm_reference_only(client, draft["id"])
    assert confirmed.status_code == 200
    assert confirmed.json()["action_type"] == "reference_only"
    activated = client.post(
        f"/api/v1/policy-drafts/{draft['id']}/activate",
        headers={"X-Demo-Actor": "admin"},
    )
    assert activated.status_code == 200
    assert activated.json()["action_type"] == "reference_only"

    quote = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": "sales_a"},
        json={"inquiry": "Quote 100 units of Product A with a 5% discount."},
    )
    assert quote.status_code == 200
    result = quote.json()
    assert result["decision"]["decision"] == "ALLOW"
    assert [item["source"] for item in result["policy_evidence"]] == ["DISCOUNT-01@v1"]
    mismatch = client.post("/api/v1/policy-diffs", json={
        "inquiry": "Quote 100 units of Product A with a 5% discount.",
        "base_policy_id": "DISCOUNT-01", "base_version": "v1",
        "compare_policy_id": "SHIPPING-REF", "compare_version": "v1",
    })
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "POLICY_ACTION_MISMATCH"


def test_guard_rejects_reference_only_policy_without_writing_quote(client):
    draft = _upload_policy(client, "SHIPPING-REF", "v1").json()
    assert _confirm_reference_only(client, draft["id"]).status_code == 200
    assert client.post(
        f"/api/v1/policy-drafts/{draft['id']}/activate",
        headers={"X-Demo-Actor": "admin"},
    ).status_code == 200

    with client.app.state.session_factory() as db:
        actor = db.get(DemoActor, "sales_a")
        customer = db.get(Customer, "C-001")
        product = db.get(Product, "P-100")
        policy = db.scalar(select(Policy).where(Policy.policy_id == "SHIPPING-REF"))
        decision, breakdown = evaluate_quote(
            actor, customer, product, policy,
            ActionProposal(customer_id=customer.id, product_id=product.id, quantity=10, requested_discount_pct=Decimal("5")),
        )
        assert decision.decision == "DENY"
        assert decision.reason_code == "POLICY_ACTION_MISMATCH"
        assert breakdown is None
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == 0


def test_policy_id_cannot_change_action_scope_between_versions(client):
    draft = _upload_policy(client, "DISCOUNT-01", "reference-v4").json()
    assert _confirm_reference_only(client, draft["id"]).status_code == 200
    activated = client.post(
        f"/api/v1/policy-drafts/{draft['id']}/activate",
        headers={"X-Demo-Actor": "admin"},
    )
    assert activated.status_code == 409
    assert activated.json()["detail"]["code"] == "POLICY_ACTION_MISMATCH"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Policy).where(Policy.version == "reference-v4")) is None


def test_policy_id_action_scope_cannot_be_changed_on_uploaded_version(client):
    draft = _upload_policy(client, "DISCOUNT-01", "reference-v4").json()
    assert _confirm_reference_only(client, draft["id"]).status_code == 200
    activated = client.post(
        f"/api/v1/policy-drafts/{draft['id']}/activate",
        headers={"X-Demo-Actor": "admin"},
    )
    assert activated.status_code == 409
    assert activated.json()["detail"]["code"] == "POLICY_ACTION_MISMATCH"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Policy).where(Policy.version == "reference-v4")) is None
