from app.db.models import DemoActor, PolicyAuditEvent
from sqlalchemy.orm import Session


def add_policy_audit_event(
    db: Session,
    actor: DemoActor,
    action: str,
    outcome: str,
    reason_code: str | None = None,
    policy_id: str | None = None,
    version: str | None = None,
) -> None:
    db.add(PolicyAuditEvent(
        tenant_id=actor.tenant_id,
        actor_id=actor.id,
        action=action,
        outcome=outcome,
        reason_code=reason_code,
        policy_id=policy_id,
        version=version,
    ))
