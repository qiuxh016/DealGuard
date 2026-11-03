from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.seed import reset_demo_data
from app.services.actors import get_current_actor
from app.services.permissions import require_capability
from app.db.models import DemoActor
from app.core.config import settings

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/reset")
def reset_demo(
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> dict[str, str]:
    if settings.app_env == "production":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Resource not found."})
    require_capability(actor, "demo.reset", code="ADMIN_REQUIRED", message="Only the demo admin can reset demo data.")
    reset_demo_data(db, actor.tenant_id)
    return {"status": "reset", "message": "Demo data restored to policy v1 and default records."}
