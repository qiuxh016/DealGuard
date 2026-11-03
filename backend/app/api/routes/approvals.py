from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.deal import ApprovalOutcome, ApprovalRejectRequest, ApprovalRequestResponse
from app.services.actors import get_current_actor
from app.services.permissions import require_capability
from app.db.models import DemoActor
from app.services.approvals import list_pending, process_approval

router = APIRouter(prefix="/approvals", tags=["approvals"])

@router.get("", response_model=list[ApprovalRequestResponse])
def get_approvals(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "approvals.read", code="MANAGER_REQUIRED", message="Only the demo manager can view pending approvals.")
    return list_pending(db, actor.tenant_id)

@router.post("/{approval_id}/approve", response_model=ApprovalOutcome)
def approve(approval_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    return process_approval(db, actor, approval_id, True)

@router.post("/{approval_id}/reject", response_model=ApprovalOutcome)
def reject(approval_id: str, body: ApprovalRejectRequest, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    return process_approval(db, actor, approval_id, False, body.reason)
