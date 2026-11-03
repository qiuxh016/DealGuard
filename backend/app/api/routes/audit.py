from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DemoActor, PolicyAuditEvent
from app.services.actors import get_current_actor
from app.services.permissions import require_capability

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_policy_audit_events(
    action: str | None = Query(default=None, max_length=80),
    outcome: str | None = Query(default=None, max_length=16),
    policy_id: str | None = Query(default=None, max_length=80),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "audit.read", code="AUDIT_ROLE_REQUIRED", message="Only workspace administrators can view policy management audit events.")
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TIME_RANGE", "message": "created_from must be earlier than created_to."})
    query = select(PolicyAuditEvent).where(PolicyAuditEvent.tenant_id == actor.tenant_id)
    if action:
        query = query.where(PolicyAuditEvent.action == action)
    if outcome:
        query = query.where(PolicyAuditEvent.outcome == outcome)
    if policy_id:
        query = query.where(PolicyAuditEvent.policy_id == policy_id)
    if created_from:
        query = query.where(PolicyAuditEvent.created_at >= created_from)
    if created_to:
        query = query.where(PolicyAuditEvent.created_at <= created_to)
    rows = db.scalars(query.order_by(PolicyAuditEvent.created_at.desc()).offset(offset).limit(limit)).all()
    count_query = select(PolicyAuditEvent.id).where(PolicyAuditEvent.tenant_id == actor.tenant_id)
    if action:
        count_query = count_query.where(PolicyAuditEvent.action == action)
    if outcome:
        count_query = count_query.where(PolicyAuditEvent.outcome == outcome)
    if policy_id:
        count_query = count_query.where(PolicyAuditEvent.policy_id == policy_id)
    if created_from:
        count_query = count_query.where(PolicyAuditEvent.created_at >= created_from)
    if created_to:
        count_query = count_query.where(PolicyAuditEvent.created_at <= created_to)
    total = db.scalar(select(func.count()).select_from(count_query.subquery())) or 0
    return {
        "total": total,
        "items": [{
            "id": row.id, "actor_id": row.actor_id, "action": row.action,
            "outcome": row.outcome, "reason_code": row.reason_code,
            "policy_id": row.policy_id, "version": row.version, "created_at": row.created_at,
        } for row in rows],
        "limit": limit, "offset": offset,
    }
