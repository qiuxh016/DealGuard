from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ApprovalRequest, Customer, DemoActor, Policy, PolicyDraft, Product, QuoteDraft, RunRecord, Tenant

TENANT_ID = "harborlight-demo"
POLICY_ID = "DISCOUNT-01"
POLICY_DIR = Path(__file__).resolve().parents[3] / "data" / "policies"
POLICY_TEXT = {version: (POLICY_DIR / f"{version}.md").read_text(encoding="utf-8-sig").strip() for version in ("v1", "v2", "v3")}


def seed_demo_data(db: Session) -> None:
    if db.get(Tenant, TENANT_ID) is None:
        db.add(Tenant(id=TENANT_ID, name="HarborLight Trading"))
        db.flush()
    is_new_demo = not db.get(Customer, "C-001")
    if is_new_demo:
        db.add_all([
            Customer(id="C-001", name="HarborLight VIP Buyer", tier="VIP", tenant_id=TENANT_ID),
            Customer(id="C-002", name="HarborLight Standard Buyer", tier="Standard", tenant_id=TENANT_ID),
            Product(id="P-100", name="Product A", currency="USD", unit_price=Decimal("100.00"), stock=2000),
            Policy(
                policy_id=POLICY_ID, version="v1", title="VIP discount limit v1", status="active",
                customer_tier="VIP", max_discount_pct=Decimal("10.00"), exception_approval_allowed=False,
                effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc), source=POLICY_TEXT["v1"],
            ),
            Policy(
                policy_id=POLICY_ID, version="v2", title="VIP discount limit v2", status="inactive",
                customer_tier="VIP", max_discount_pct=Decimal("5.00"), exception_approval_allowed=False,
                effective_from=datetime(2026, 11, 1, tzinfo=timezone.utc), source=POLICY_TEXT["v2"],
            ),
            Policy(
                policy_id=POLICY_ID, version="v3", title="VIP discount limit v3 with manager review", status="inactive",
                customer_tier="VIP", max_discount_pct=Decimal("5.00"), review_max_discount_pct=Decimal("8.00"),
                exception_approval_allowed=True, effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc), source=POLICY_TEXT["v3"],
            ),
        ])
        for actor_id, name, role, customer_id in (("sales_a", "Sales A", "sales", "C-001"), ("sales_b", "Sales B", "sales", "C-002"), ("admin", "Demo Admin", "admin", None), ("manager", "Demo Manager", "manager", None)):
            actor = db.get(DemoActor, actor_id)
            if actor is None:
                db.add(DemoActor(id=actor_id, display_name=name, role=role, customer_id=customer_id, tenant_id=TENANT_ID))
            else:
                actor.display_name, actor.role, actor.customer_id, actor.tenant_id = name, role, customer_id, TENANT_ID
    if not is_new_demo and not db.get(DemoActor, "manager"):
        db.add(DemoActor(id="manager", display_name="Demo Manager", role="manager", customer_id=None))
    if not is_new_demo and not db.scalar(select(Policy).where(Policy.policy_id == POLICY_ID, Policy.version == "v3")):
        db.add(Policy(
            policy_id=POLICY_ID, version="v3", title="VIP discount limit v3 with manager review", status="inactive",
            customer_tier="VIP", max_discount_pct=Decimal("5.00"), review_max_discount_pct=Decimal("8.00"),
            exception_approval_allowed=True, effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc), source=POLICY_TEXT["v3"],
        ))
    db.commit()


def reset_demo_data(db: Session, tenant_id: str = TENANT_ID) -> None:
    for actor in db.scalars(select(DemoActor).where(DemoActor.tenant_id == tenant_id)).all():
        actor.customer_id = None
    db.flush()
    for model in (ApprovalRequest, PolicyDraft, RunRecord, QuoteDraft, Policy, Product, Customer):
        db.execute(delete(model).where(model.tenant_id == tenant_id))
    db.commit()
    if tenant_id == TENANT_ID:
        seed_demo_data(db)
