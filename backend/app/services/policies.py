from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DemoActor, Policy
from app.schemas.deal import PolicyResponse


def list_policies(db: Session, tenant_id: str) -> list[PolicyResponse]:
    rows = db.scalars(select(Policy).where(Policy.tenant_id == tenant_id).order_by(Policy.policy_id, Policy.version)).all()
    return [
        PolicyResponse(
            id=row.id, policy_id=row.policy_id, version=row.version, title=row.title,
            status=row.status, max_discount_pct=row.max_discount_pct,
            review_max_discount_pct=row.review_max_discount_pct,
            exception_approval_allowed=row.exception_approval_allowed, source=row.source,
        )
        for row in rows
    ]


def activate_policy(db: Session, actor: DemoActor, policy_row_id: str) -> PolicyResponse:
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "Only the demo admin can activate a policy."})
    selected = db.scalar(select(Policy).where(Policy.id == policy_row_id, Policy.tenant_id == actor.tenant_id))
    if selected is None:
        raise HTTPException(status_code=404, detail={"code": "POLICY_NOT_FOUND", "message": "Policy version not found."})
    now = datetime.now(timezone.utc)
    if selected.effective_from.replace(tzinfo=timezone.utc) > now:
        raise HTTPException(status_code=409, detail={"code": "POLICY_NOT_EFFECTIVE", "message": "This policy version is not effective yet."})
    versions = db.scalars(select(Policy).where(Policy.policy_id == selected.policy_id, Policy.tenant_id == actor.tenant_id)).all()
    for version in versions:
        version.status = "active" if version.id == selected.id else "inactive"
    db.commit()
    db.refresh(selected)
    return PolicyResponse(
        id=selected.id, policy_id=selected.policy_id, version=selected.version,
        title=selected.title, status=selected.status,
        max_discount_pct=selected.max_discount_pct,
        review_max_discount_pct=selected.review_max_discount_pct,
        exception_approval_allowed=selected.exception_approval_allowed,
        source=selected.source,
    )
