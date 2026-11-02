from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.workflow import _parse_intent
from app.db.database import get_db
from app.db.models import Customer, DemoActor, Policy, PolicyDraft, Product
from app.guards.business_rules import evaluate_quote
from app.schemas.deal import ActionProposal, GuardDecision, InquiryIntent, MetricsResponse, PolicyDiffRequest, PolicyDiffResponse, PolicyDiffSide, PolicyDraftResponse, PolicyDraftUpdate
from app.db.models import RunRecord
from app.services.actors import get_current_actor
from app.services.policies import activate_policy

router = APIRouter(tags=["policy governance", "metrics"])
MAX_UPLOAD_BYTES = 1024 * 1024


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    if actor.role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail={"code": "METRICS_ROLE_REQUIRED", "message": "Only the demo manager or admin can view run metrics."})
    rows = db.scalars(select(RunRecord).where(RunRecord.tenant_id == actor.tenant_id)).all()
    decisions = [(row.result or {}).get("decision", {}).get("decision") for row in rows]
    durations = [row.duration_ms for row in rows if row.duration_ms is not None]
    input_values = [row.input_tokens for row in rows if row.input_tokens is not None]
    output_values = [row.output_tokens for row in rows if row.output_tokens is not None]
    return MetricsResponse(run_count=len(rows), allow_count=decisions.count("ALLOW"), review_count=decisions.count("REVIEW"),
        deny_count=decisions.count("DENY"), needs_input_count=decisions.count("NEEDS_INPUT"),
        failed_count=sum(row.status == "failed" for row in rows),
        avg_duration_ms=(sum(durations) / len(durations) if durations else None),
        input_tokens=sum(input_values) if input_values else None, output_tokens=sum(output_values) if output_values else None)


def _admin(actor: DemoActor):
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "Only the demo admin can manage policy drafts."})


def _draft_response(row: PolicyDraft) -> PolicyDraftResponse:
    return PolicyDraftResponse(id=row.id, policy_id=row.policy_id, version=row.version, title=row.title,
        customer_tier=row.customer_tier, max_discount_pct=row.max_discount_pct,
        review_max_discount_pct=row.review_max_discount_pct, exception_approval_allowed=row.exception_approval_allowed,
        status=row.status, source=row.source)


@router.post("/policy-drafts", response_model=PolicyDraftResponse)
async def upload_policy_draft(policy_id: str = Form(min_length=1, max_length=80), version: str = Form(min_length=1, max_length=40),
        file: UploadFile = File(...), actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor)
    policy_id, version = policy_id.strip(), version.strip()
    if not policy_id or not version:
        raise HTTPException(status_code=422, detail={"code": "POLICY_ID_REQUIRED", "message": "Policy ID and version cannot be blank."})
    if Path(file.filename or "").suffix.lower() not in {".md", ".txt"}:
        raise HTTPException(status_code=415, detail={"code": "UNSUPPORTED_POLICY_FILE", "message": "Upload a UTF-8 .md or .txt file."})
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"code": "POLICY_FILE_TOO_LARGE", "message": "Policy source must be at most 1 MiB."})
    if not content.strip():
        raise HTTPException(status_code=422, detail={"code": "EMPTY_POLICY_FILE", "message": "Policy source cannot be empty."})
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_POLICY_ENCODING", "message": "Policy source must use UTF-8."}) from exc
    row = PolicyDraft(policy_id=policy_id.strip(), version=version.strip(), source=source, uploaded_by=actor.id, tenant_id=actor.tenant_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _draft_response(row)


@router.put("/policy-drafts/{draft_id}", response_model=PolicyDraftResponse)
def edit_policy_draft(draft_id: str, body: PolicyDraftUpdate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor)
    row = db.scalar(select(PolicyDraft).where(PolicyDraft.id == draft_id, PolicyDraft.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "POLICY_DRAFT_NOT_FOUND", "message": "Policy draft not found."})
    if row.status != "draft":
        raise HTTPException(status_code=409, detail={"code": "POLICY_DRAFT_LOCKED", "message": "Activated drafts cannot be edited."})
    if body.exception_approval_allowed and (body.review_max_discount_pct is None or body.review_max_discount_pct <= body.max_discount_pct):
        raise HTTPException(status_code=422, detail={"code": "INVALID_REVIEW_LIMIT", "message": "Review limit must exceed the direct discount limit."})
    if not body.exception_approval_allowed and body.review_max_discount_pct is not None:
        raise HTTPException(status_code=422, detail={"code": "UNUSED_REVIEW_LIMIT", "message": "Set the review limit only when manager approval exceptions are enabled."})
    for field in ("title", "customer_tier", "max_discount_pct", "review_max_discount_pct", "exception_approval_allowed"):
        setattr(row, field, getattr(body, field))
    db.commit()
    db.refresh(row)
    return _draft_response(row)


@router.post("/policy-drafts/{draft_id}/activate", response_model=PolicyDraftResponse)
def activate_draft(draft_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor)
    row = db.scalar(select(PolicyDraft).where(PolicyDraft.id == draft_id, PolicyDraft.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "POLICY_DRAFT_NOT_FOUND", "message": "Policy draft not found."})
    if row.status == "active" and row.policy_row_id:
        return _draft_response(row)
    if row.max_discount_pct is None or (row.exception_approval_allowed and row.review_max_discount_pct is None):
        raise HTTPException(status_code=409, detail={"code": "POLICY_RULES_INCOMPLETE", "message": "Confirm structured discount rules before activation."})
    duplicate = db.scalar(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == row.policy_id, Policy.version == row.version))
    if duplicate:
        raise HTTPException(status_code=409, detail={"code": "POLICY_VERSION_EXISTS", "message": "This policy ID and version already exist."})
    policy = Policy(policy_id=row.policy_id, version=row.version, title=row.title, status="inactive",
        customer_tier=row.customer_tier, max_discount_pct=row.max_discount_pct,
        review_max_discount_pct=row.review_max_discount_pct, exception_approval_allowed=row.exception_approval_allowed,
        effective_from=datetime.now(timezone.utc), source=row.source, tenant_id=actor.tenant_id)
    db.add(policy)
    db.flush()
    row.status, row.policy_row_id = "active", policy.id
    db.commit()
    db.refresh(row)
    activate_policy(db, actor, policy.id)
    return _draft_response(row)


@router.post("/policy-diffs", response_model=PolicyDiffResponse)
def compare_policies(body: PolicyDiffRequest, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    customer = db.get(Customer, actor.customer_id) if actor.customer_id else None
    try:
        parsed = _parse_intent({"raw_inquiry": body.inquiry, "events": []})
        intent = InquiryIntent.model_validate(parsed["intent"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "POLICY_DIFF_PARSE_FAILED", "message": "The inquiry could not be parsed; no comparison was run."}) from exc
    product = db.get(Product, intent.product_id) if intent.product_id else None
    base = db.scalar(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == body.base_policy_id, Policy.version == body.base_version))
    compare = db.scalar(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == body.compare_policy_id, Policy.version == body.compare_version))
    if not base or not compare:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND", "message": "Both registered policy versions are required."})
    if base.customer_tier != compare.customer_tier:
        raise HTTPException(status_code=422, detail={"code": "POLICY_SCOPE_MISMATCH", "message": "Policy versions must apply to the same customer tier."})
    proposal = ActionProposal(customer_id=customer.id if customer else "", product_id=intent.product_id or "", quantity=intent.quantity or 1,
                              requested_discount_pct=intent.requested_discount_pct or Decimal("0"))
    missing = [name for name in ("product_id", "quantity", "requested_discount_pct") if getattr(intent, name) is None]
    def side(policy: Policy) -> PolicyDiffSide:
        if missing:
            decision = GuardDecision(decision="NEEDS_INPUT", reason_code="MISSING_REQUIRED_FIELDS", policy_id=policy.policy_id,
                                     policy_version=policy.version, human_message="Inquiry is missing: " + ", ".join(missing))
            breakdown = None
        elif customer is None or customer.tier != policy.customer_tier or product is None:
            decision = GuardDecision(decision="DENY", reason_code="POLICY_NOT_APPLICABLE", policy_id=policy.policy_id,
                                     policy_version=policy.version, human_message="This policy does not apply to the trusted demo actor or requested product.")
            breakdown = None
        else:
            decision, breakdown = evaluate_quote(actor, customer, product, policy, proposal)
        return PolicyDiffSide(policy_id=policy.policy_id, version=policy.version, title=policy.title,
                              citation=policy.source, decision=decision, breakdown=breakdown)
    return PolicyDiffResponse(intent=intent, base=side(base), compare=side(compare))
