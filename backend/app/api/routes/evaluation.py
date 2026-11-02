import hashlib
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.models import DemoActor
from app.rag.evaluation import evaluate_offline
from app.services.actors import get_current_actor

router = APIRouter(prefix="/evaluation", tags=["evaluation"])
DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "policy_retrieval_eval"


@router.get("")
def get_evaluation_report(
    split: Literal["all", "development", "holdout"] = Query(default="all"),
    actor: DemoActor = Depends(get_current_actor),
):
    if actor.role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "EVALUATION_ROLE_REQUIRED", "message": "Only the demo manager or admin can view evaluation reports."})
    cases_path = DATA_DIR / "cases.jsonl"
    policies_path = DATA_DIR / "policies.json"
    if not cases_path.is_file() or not policies_path.is_file():
        raise HTTPException(status_code=503, detail={"code": "EVALUATION_DATA_UNAVAILABLE", "message": "The policy evaluation dataset is not installed."})
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if split != "all":
        cases = [case for case in cases if case["split"] == split]
    policies = json.loads(policies_path.read_text(encoding="utf-8"))["policies"]
    return {
        "dataset_sha256": hashlib.sha256(cases_path.read_bytes() + policies_path.read_bytes()).hexdigest(),
        "split": split,
        **evaluate_offline(cases, policies),
    }
