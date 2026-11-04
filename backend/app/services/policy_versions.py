from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Policy, PolicyDraft, PolicyVersionKey


def reserve_policy_version(db: Session, tenant_id: str, policy_id: str, version: str) -> bool:
    """Claim a tenant policy version across draft and registered-policy tables."""
    registered = db.scalar(select(Policy.id).where(
        Policy.tenant_id == tenant_id, Policy.policy_id == policy_id, Policy.version == version,
    ))
    draft = db.scalar(select(PolicyDraft.id).where(
        PolicyDraft.tenant_id == tenant_id, PolicyDraft.policy_id == policy_id, PolicyDraft.version == version,
    ))
    if registered or draft:
        return False
    existing = db.scalar(select(PolicyVersionKey.id).where(
        PolicyVersionKey.tenant_id == tenant_id,
        PolicyVersionKey.policy_id == policy_id,
        PolicyVersionKey.version == version,
    ))
    if existing:
        return False
    try:
        with db.begin_nested():
            db.add(PolicyVersionKey(id=str(uuid4()), tenant_id=tenant_id, policy_id=policy_id, version=version))
            db.flush()
    except IntegrityError:
        return False
    return True
