from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.workflow import _infer_policy_scope
from app.db.models import Customer, DemoActor, Policy, Product, QuoteDraft
from app.guards.business_rules import evaluate_quote
from app.schemas.deal import ActionProposal, PolicyDraftUpdate


@pytest.mark.parametrize("inquiry, expected", [
    ("bulk wholesale order", "bulk_order"),
    ("loyal repeat customer", "loyalty"),
    ("提前付款", "early_payment"),
    ("paying upfront in cash before shipment", "early_payment"),
    ("holiday campaign", "seasonal_campaign"),
    ("product-specific category", "product_specific"),
    ("regular quote", "general"),
    ("bulk seasonal campaign", None),
])
def test_policy_scope_is_derived_from_inquiry(inquiry, expected):
    assert _infer_policy_scope(inquiry) == expected


def test_guard_rejects_policy_scope_mismatch_without_writing(client):
    with client.app.state.session_factory() as db:
        actor = db.get(DemoActor, "sales_a")
        customer = db.get(Customer, "C-001")
        product = db.scalar(select(Product).where(Product.id == "P-100"))
        policy = Policy(
            policy_id="BULK-ONLY", version="v1", title="Bulk only", action_type="quote_discount",
            policy_scope="bulk_order", status="active", customer_tier="VIP", max_discount_pct=Decimal("10"),
            exception_approval_allowed=False, effective_from=datetime.now(timezone.utc), source="Bulk orders only.",
            tenant_id="harborlight-demo",
        )
        db.add(policy)
        db.commit()
        before = db.scalar(select(QuoteDraft).where(QuoteDraft.tenant_id == "harborlight-demo"))
        decision, breakdown = evaluate_quote(
            actor, customer, product, policy,
            ActionProposal(customer_id=customer.id, product_id=product.id, quantity=1, requested_discount_pct=Decimal("1")),
            "loyalty",
        )
        assert decision.reason_code == "POLICY_SCOPE_MISMATCH"
        assert decision.decision == "DENY"
        assert breakdown is None
        assert before is None
        assert db.scalar(select(QuoteDraft).where(QuoteDraft.tenant_id == "harborlight-demo")) is None


def test_product_specific_policy_draft_requires_product_id():
    with pytest.raises(ValidationError):
        PolicyDraftUpdate(title="P specific", action_type="quote_discount", policy_scope="product_specific", max_discount_pct=5)
    draft = PolicyDraftUpdate(title="P specific", action_type="quote_discount", policy_scope="product_specific", scope_product_id="P-100", max_discount_pct=5)
    assert draft.scope_product_id == "P-100"


def test_reference_policy_cannot_have_executable_scope():
    with pytest.raises(ValidationError):
        PolicyDraftUpdate(title="Reference", action_type="reference_only", policy_scope="bulk_order")


def test_admin_cannot_activate_product_scope_for_unknown_product(client):
    draft_response = client.post(
        "/api/v1/policy-drafts", headers={"X-Demo-Actor": "admin"},
        data={"policy_id": "PRODUCT-ONLY", "version": "v1"},
        files={"file": ("policy.md", "Product discount terms", "text/markdown")},
    )
    assert draft_response.status_code == 200
    draft = draft_response.json()
    update = client.put(
        f"/api/v1/policy-drafts/{draft['id']}", headers={"X-Demo-Actor": "admin"},
        json={"title": "Product only", "action_type": "quote_discount", "policy_scope": "product_specific",
              "scope_product_id": "P-404", "customer_tier": "VIP", "max_discount_pct": 5,
              "review_max_discount_pct": None, "exception_approval_allowed": False},
    )
    assert update.status_code == 200
    activation = client.post(f"/api/v1/policy-drafts/{draft['id']}/activate", headers={"X-Demo-Actor": "admin"})
    assert activation.status_code == 409
    assert activation.json()["detail"]["code"] == "POLICY_SCOPE_PRODUCT_NOT_FOUND"
    with client.app.state.session_factory() as db:
        assert db.scalar(select(Policy).where(Policy.policy_id == "PRODUCT-ONLY")) is None
