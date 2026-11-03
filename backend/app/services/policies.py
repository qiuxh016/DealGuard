from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DemoActor, Policy, Product
from app.schemas.deal import PolicyResponse
from app.services.policy_audit import add_policy_audit_event
from app.services.permissions import has_capability


def list_policies(db: Session, tenant_id: str) -> list[PolicyResponse]:
    rows = db.scalars(select(Policy).where(Policy.tenant_id == tenant_id).order_by(Policy.policy_id, Policy.version)).all()
    return [
        PolicyResponse(
            id=row.id, policy_id=row.policy_id, version=row.version, title=row.title,
            action_type=row.action_type,
            policy_scope=row.policy_scope, scope_product_id=row.scope_product_id,
            status=row.status, max_discount_pct=row.max_discount_pct,
            review_max_discount_pct=row.review_max_discount_pct,
            exception_approval_allowed=row.exception_approval_allowed, source=row.source,
        )
        for row in rows
    ]


def is_policy_effective(policy: Policy, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    starts = policy.effective_from.replace(tzinfo=timezone.utc) if policy.effective_from.tzinfo is None else policy.effective_from
    ends = policy.effective_to
    if ends is not None and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return starts <= now and (ends is None or now < ends)


def current_quote_policies(
    db: Session,
    tenant_id: str,
    customer_tier: str,
    policy_id: str | None = None,
    now: datetime | None = None,
) -> list[Policy]:
    query = select(Policy).where(
        Policy.tenant_id == tenant_id,
        Policy.action_type == "quote_discount",
        Policy.customer_tier == customer_tier,
        Policy.status == "active",
    )
    if policy_id is not None:
        query = query.where(Policy.policy_id == policy_id)
    instant = now or datetime.now(timezone.utc)
    return [row for row in db.scalars(query).all() if is_policy_effective(row, instant)]


def activate_policy(db: Session, actor: DemoActor, policy_row_id: str) -> PolicyResponse:
    if not has_capability(actor, "policies.activate"):
        add_policy_audit_event(db, actor, "policy_activation_attempt", "DENIED", "ADMIN_REQUIRED")
        db.commit()
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "Only workspace administrators can activate a policy."})
    selected = db.scalar(select(Policy).where(Policy.id == policy_row_id, Policy.tenant_id == actor.tenant_id))
    if selected is None:
        add_policy_audit_event(db, actor, "policy_activation_attempt", "DENIED", "POLICY_NOT_FOUND")
        db.commit()
        raise HTTPException(status_code=404, detail={"code": "POLICY_NOT_FOUND", "message": "Policy version not found."})
    if selected.policy_scope == "product_specific" and db.scalar(select(Product.id).where(
        Product.id == selected.scope_product_id, Product.tenant_id == actor.tenant_id,
    )) is None:
        raise HTTPException(status_code=409, detail={"code": "POLICY_SCOPE_PRODUCT_NOT_FOUND", "message": "The scoped product does not exist in this tenant."})
    now = datetime.now(timezone.utc)
    if selected.effective_from.replace(tzinfo=timezone.utc) > now:
        raise HTTPException(status_code=409, detail={"code": "POLICY_NOT_EFFECTIVE", "message": "This policy version is not effective yet."})
    versions = db.scalars(select(Policy).where(Policy.policy_id == selected.policy_id, Policy.tenant_id == actor.tenant_id)).all()
    if any(version.action_type != selected.action_type for version in versions):
        raise HTTPException(status_code=409, detail={"code": "POLICY_ACTION_MISMATCH", "message": "Versions of one policy ID must share an action scope."})
    if any(version.policy_scope != selected.policy_scope or version.scope_product_id != selected.scope_product_id for version in versions):
        raise HTTPException(status_code=409, detail={"code": "POLICY_SCOPE_MISMATCH", "message": "Versions of one policy ID must keep the same applicability scope."})
    for version in versions:
        version.status = "active" if version.id == selected.id else "inactive"
    add_policy_audit_event(db, actor, "policy_activated", "SUCCESS", policy_id=selected.policy_id, version=selected.version)
    db.commit()
    db.refresh(selected)
    return PolicyResponse(
        id=selected.id, policy_id=selected.policy_id, version=selected.version,
        title=selected.title, action_type=selected.action_type, status=selected.status,
        policy_scope=selected.policy_scope, scope_product_id=selected.scope_product_id,
        max_discount_pct=selected.max_discount_pct,
        review_max_discount_pct=selected.review_max_discount_pct,
        exception_approval_allowed=selected.exception_approval_allowed,
        source=selected.source,
    )
