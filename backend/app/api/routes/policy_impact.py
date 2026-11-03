from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DemoActor, PolicyDraft
from app.schemas.deal import PolicyImpactReport, PolicyImpactRequest
from app.services.actors import get_current_actor
from app.services.permissions import require_capability
from app.services.policy_impact import simulate_policy_impact

router = APIRouter(prefix="/policy-impact", tags=["policy impact"])


@router.post("/simulate", response_model=PolicyImpactReport)
def simulate(
    body: PolicyImpactRequest,
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(
        actor, "policy_drafts.manage", code="ADMIN_REQUIRED",
        message="Only workspace administrators can simulate policy changes.",
    )
    draft = db.scalar(select(PolicyDraft).where(
        PolicyDraft.id == body.policy_draft_id,
        PolicyDraft.tenant_id == actor.tenant_id,
    ))
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "POLICY_DRAFT_NOT_FOUND", "message": "Policy draft not found."})
    if draft.action_type is None or draft.status != "draft":
        raise HTTPException(status_code=409, detail={"code": "POLICY_DRAFT_NOT_READY", "message": "Only a structured, inactive draft can be simulated."})
    if draft.action_type == "quote_discount" and draft.max_discount_pct is None:
        raise HTTPException(status_code=409, detail={"code": "POLICY_RULES_INCOMPLETE", "message": "The draft needs a structured discount limit before simulation."})
    report = simulate_policy_impact(db, actor.tenant_id, draft, body.run_ids, body.mode)
    return PolicyImpactReport.model_validate(report)
