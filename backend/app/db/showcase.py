"""Create a persistent, isolated workspace for product walkthroughs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.migrations import upgrade_database
from app.db.models import (
    AuthAccount,
    Customer,
    DemoActor,
    Policy,
    PolicyAuditEvent,
    PolicyDraft,
    Product,
    Tenant,
)
from app.db.seed import POLICY_DIR
from app.services.actors import hash_password


TENANT_ID = "dealguard-showcase-workspace"
DEFAULT_EMAIL = "demo@dealguard.local"
SHOWCASE_PASSWORD = "DealGuard-Showcase-2026!"
ADMIN_ID = "dealguard-showcase-admin"
CUSTOMERS = (
    ("DG-C-001", "HarborLight VIP Retail", "VIP"),
    ("DG-C-002", "Northstar Electronics", "VIP"),
    ("DG-C-003", "Metro Parts Supply", "Standard"),
    ("DG-C-004", "Pacific Industrial Group", "Standard"),
    ("DG-C-005", "BluePeak Distribution", "VIP"),
)
SALES_RUNNERS = {
    customer_id: f"showcase_sales_{index:03d}"
    for index, (customer_id, _, _) in enumerate(CUSTOMERS, start=1)
}

PRODUCTS = (
    ("P-100", "Industrial LED Panel", "USD", "100.00", 5000),
    ("P-200", "Smart Sensor Module", "USD", "250.00", 2400),
    ("P-300", "Control Hub Pro", "USD", "480.00", 750),
    ("P-400", "Power Supply Unit", "USD", "60.00", 8000),
    ("P-500", "Edge Gateway", "USD", "1250.00", 320),
    ("P-600", "Industrial Camera", "USD", "720.00", 640),
    ("P-700", "Limited Edition Controller", "USD", "980.00", 12),
    ("P-800", "Backorder Demo Unit", "USD", "340.00", 0),
)


def _seed_business_data(db, actor: DemoActor, sales_runners: list[DemoActor]) -> None:
    now = datetime.now(timezone.utc)
    vip_v1 = (POLICY_DIR / "v1.md").read_text(encoding="utf-8-sig").strip()
    vip_v2 = (POLICY_DIR / "v2.md").read_text(encoding="utf-8-sig").strip()
    vip_v3 = (POLICY_DIR / "v3.md").read_text(encoding="utf-8-sig").strip()
    standard_source = (
        "# Standard Customer Discount Policy — v1\n\n"
        "Verified Standard customers may receive a direct discount up to 3%. "
        "Discounts above 3% are denied. A quote is a non-binding draft."
    )
    db.add(Tenant(id=TENANT_ID, name="HarborLight Trading · Showcase"))
    db.flush()
    db.add_all([
        Customer(id=customer_id, name=name, tier=tier, tenant_id=TENANT_ID)
        for customer_id, name, tier in CUSTOMERS
    ])
    db.flush()
    db.add(actor)
    db.add_all(sales_runners)
    db.flush()
    db.add_all([
        Product(sku=sku, name=name, currency=currency, unit_price=Decimal(price), stock=stock, tenant_id=TENANT_ID)
        for sku, name, currency, price, stock in PRODUCTS
    ])
    db.flush()

    policies = (
        Policy(policy_id="SHOW-01-VIP-DISCOUNT", version="v1", title="VIP discount limit v1", action_type="quote_discount", policy_scope="general", status="inactive", customer_tier="VIP", max_discount_pct=Decimal("10.00"), exception_approval_allowed=False, effective_from=now - timedelta(days=180), source=vip_v1, tenant_id=TENANT_ID),
        Policy(policy_id="SHOW-01-VIP-DISCOUNT", version="v2", title="VIP discount limit v2", action_type="quote_discount", policy_scope="general", status="inactive", customer_tier="VIP", max_discount_pct=Decimal("5.00"), exception_approval_allowed=False, effective_from=now - timedelta(days=90), source=vip_v2, tenant_id=TENANT_ID),
        Policy(policy_id="SHOW-01-VIP-DISCOUNT", version="v3", title="VIP discount limit v3 with manager review", action_type="quote_discount", policy_scope="general", status="active", customer_tier="VIP", max_discount_pct=Decimal("5.00"), review_max_discount_pct=Decimal("8.00"), exception_approval_allowed=True, effective_from=now - timedelta(days=30), source=vip_v3, tenant_id=TENANT_ID),
        Policy(policy_id="SHOW-02-STANDARD-DISCOUNT", version="v1", title="Standard customer discount limit", action_type="quote_discount", policy_scope="general", status="active", customer_tier="Standard", max_discount_pct=Decimal("3.00"), exception_approval_allowed=False, effective_from=now - timedelta(days=120), source=standard_source, tenant_id=TENANT_ID),
        Policy(policy_id="SHOW-90-SHIPPING-TERMS", version="v1", title="Shipping and delivery reference", action_type="reference_only", policy_scope="general", status="active", customer_tier="VIP", max_discount_pct=None, exception_approval_allowed=False, effective_from=now - timedelta(days=60), source="# Shipping Reference\n\nStandard dispatch is within five business days after written quote acceptance. Freight terms are confirmed before order placement.", tenant_id=TENANT_ID),
    )
    db.add_all(policies)
    db.add_all([
        PolicyDraft(policy_id="SHOW-SEASONAL", version="v1", title="Summer volume promotion", action_type="quote_discount", policy_scope="seasonal_campaign", customer_tier="VIP", max_discount_pct=Decimal("4.00"), review_max_discount_pct=None, exception_approval_allowed=False, source="VIP seasonal campaign offers a direct discount up to 4% on eligible summer orders.", status="draft", uploaded_by=actor.id, tenant_id=TENANT_ID),
        PolicyDraft(policy_id="SHOW-PRODUCT", version="v1", title="Control Hub launch promotion", action_type="quote_discount", policy_scope="product_specific", scope_product_id="P-300", customer_tier="VIP", max_discount_pct=Decimal("4.00"), review_max_discount_pct=None, exception_approval_allowed=False, source="Verified VIP customers may receive up to 4% off Control Hub Pro (P-300) during the launch period.", status="draft", uploaded_by=actor.id, tenant_id=TENANT_ID),
        PolicyDraft(policy_id="SHOW-RETURNS", version="v1", title="Returns and warranty reference", action_type="reference_only", policy_scope="general", customer_tier="Standard", max_discount_pct=None, review_max_discount_pct=None, exception_approval_allowed=False, source="# Returns and Warranty\n\nStandard products include a 12-month limited warranty. Return eligibility is assessed against the signed sales terms.", status="draft", uploaded_by=actor.id, tenant_id=TENANT_ID),
    ])
    db.add_all([
        PolicyAuditEvent(tenant_id=TENANT_ID, actor_id=actor.id, action=action, outcome="SUCCESS", reason_code=None, policy_id=policy_id, version=version, created_at=now - timedelta(days=days))
        for action, policy_id, version, days in (
            ("policy_draft_uploaded", "SHOW-SEASONAL", "v1", 14),
            ("policy_draft_saved", "SHOW-SEASONAL", "v1", 13),
            ("policy_activated", "SHOW-01-VIP-DISCOUNT", "v3", 12),
            ("policy_draft_uploaded", "SHOW-PRODUCT", "v1", 8),
            ("policy_draft_saved", "SHOW-PRODUCT", "v1", 7),
            ("policy_draft_uploaded", "SHOW-RETURNS", "v1", 3),
        )
    ])
    db.commit()


def _seed_run_history(db, sales_runners: dict[str, DemoActor]) -> None:
    from app.api.routes.runs import create_run
    from app.schemas.deal import InquiryRequest

    original = (settings.llm_api_key, settings.llm_model, settings.local_embedding_model_path)
    settings.llm_api_key = ""
    settings.llm_model = ""
    settings.local_embedding_model_path = ""
    scenarios = (
        ("DG-C-001", "Please quote P-100 1000 units at 5% discount.", "ALLOW"),
        ("DG-C-002", "Please quote P-100 240 units at 4% discount; P-200 40 units at 3% discount.", "ALLOW"),
        ("DG-C-001", "Please quote P-300 25 units at 9% discount.", "DENY"),
        ("DG-C-003", "Please quote P-400 500 units at 3% discount.", "ALLOW"),
        ("DG-C-001", "Please quote P-700 99 units at 4% discount.", "DENY"),
        ("DG-C-004", "Please quote P-300 at 3% discount.", "NEEDS_INPUT"),
        ("DG-C-005", "Please quote P-500 12 units at 7% discount.", "REVIEW"),
        ("DG-C-002", "Please quote P-100 100 units at 4% discount; P-300 10 units at 4% discount.", "ALLOW"),
        ("DG-C-003", "Please quote P-200 100 units at 6% discount.", "DENY"),
    )
    try:
        for customer_id, inquiry, expected_decision in scenarios:
            response = create_run(InquiryRequest(inquiry=inquiry, customer_id=customer_id), sales_runners[customer_id], None, db)
            actual_decision = response.decision.decision if response.decision else None
            if actual_decision != expected_decision:
                raise RuntimeError(f"Showcase scenario expected {expected_decision}, got {actual_decision}: {inquiry}")
    finally:
        settings.llm_api_key, settings.llm_model, settings.local_embedding_model_path = original


def create_showcase_account(email: str = DEFAULT_EMAIL) -> tuple[str, str, bool]:
    if settings.app_env == "production":
        raise RuntimeError("Showcase account provisioning is disabled in APP_ENV=production.")
    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise ValueError("Provide a valid email address for the showcase account.")
    upgrade_database()
    with SessionLocal() as db:
        account = db.scalar(select(AuthAccount).where(AuthAccount.email == email))
        if account is not None:
            actor = db.get(DemoActor, account.actor_id)
            if actor and actor.tenant_id == TENANT_ID:
                account.password_hash = hash_password(SHOWCASE_PASSWORD)
                db.commit()
                return email, SHOWCASE_PASSWORD, False
            raise RuntimeError(f"{email} already belongs to another workspace; no data was changed.")
        if db.get(Tenant, TENANT_ID) is not None:
            raise RuntimeError(f"Showcase workspace {TENANT_ID} already exists; no data was changed.")

        actor = DemoActor(id=ADMIN_ID, display_name="DealGuard Showcase Admin", role="admin", customer_id=None, tenant_id=TENANT_ID)
        try:
            sales_runners = {
                customer_id: DemoActor(id=SALES_RUNNERS[customer_id], display_name=f"Sales Activity {index}", role="sales", customer_id=customer_id, tenant_id=TENANT_ID)
                for index, (customer_id, _, _) in enumerate(CUSTOMERS, start=1)
            }
            _seed_business_data(db, actor, list(sales_runners.values()))
            db.add(AuthAccount(actor_id=actor.id, email=email, password_hash=hash_password(SHOWCASE_PASSWORD), is_active=True))
            db.commit()
        except Exception:
            db.rollback()
            raise
        _seed_run_history(db, sales_runners)
        return email, SHOWCASE_PASSWORD, True


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Provision one persistent DealGuard showcase admin account and its workspace data.")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help=f"login email (default: {DEFAULT_EMAIL})")
    arguments = parser.parse_args()
    email, password, created = create_showcase_account(arguments.email)
    if created:
        print("Showcase workspace created in the configured database.")
    else:
        print("Showcase account password reset; workspace data was preserved.")
    print(f"Email: {email}")
    print(f"Password: {password}")


if __name__ == "__main__":
    main()
