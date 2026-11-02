from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.deal import PolicyResponse
from app.services.actors import get_current_actor
from app.db.models import DemoActor
from app.services.policies import activate_policy, list_policies

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("", response_model=list[PolicyResponse])
def get_policies(
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> list[PolicyResponse]:
    return list_policies(db, actor.tenant_id)


@router.post("/{policy_row_id}/activate", response_model=PolicyResponse)
def activate_policy_version(
    policy_row_id: str,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> PolicyResponse:
    return activate_policy(db, actor, policy_row_id)
