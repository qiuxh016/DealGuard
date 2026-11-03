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
from app.schemas.deal import ActionProposal, GuardDecision, InquiryIntent, MetricsResponse, PolicyDiffLine, PolicyDiffRequest, PolicyDiffResponse, PolicyDiffSide, PolicyDraftResponse, PolicyDraftUpdate
from app.db.models import RunRecord
from app.services.actors import get_current_actor
from app.services.permissions import has_capability, require_capability
from app.services.inventory import aggregate_inventory_failure
from app.services.policy_audit import add_policy_audit_event
from app.services.policies import activate_policy

router = APIRouter(tags=["policy governance", "metrics"])
MAX_UPLOAD_BYTES = 1024 * 1024


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "metrics.read", code="METRICS_ROLE_REQUIRED", message="Only workspace managers or administrators can view run metrics.")
    rows = db.scalars(select(RunRecord).where(RunRecord.tenant_id == actor.tenant_id)).all()
    decisions = [(row.result or {}).get("decision", {}).get("decision") for row in rows]
    durations = [row.duration_ms for row in rows if row.duration_ms is not None]
    sorted_durations = sorted(durations)
    p50_duration = sorted_durations[(len(sorted_durations) - 1) // 2] if sorted_durations else None
    p95_duration = sorted_durations[(95 * len(sorted_durations) + 99) // 100 - 1] if sorted_durations else None
    input_values = [row.input_tokens for row in rows if row.input_tokens is not None]
    output_values = [row.output_tokens for row in rows if row.output_tokens is not None]
    return MetricsResponse(run_count=len(rows), allow_count=decisions.count("ALLOW"), review_count=decisions.count("REVIEW"),
        deny_count=decisions.count("DENY"), needs_input_count=decisions.count("NEEDS_INPUT"),
        failed_count=sum(row.status == "failed" for row in rows),
        avg_duration_ms=(sum(durations) / len(durations) if durations else None),
        p50_duration_ms=p50_duration, p95_duration_ms=p95_duration,
        demo_run_count=sum(row.execution_mode == "demo" for row in rows),
        llm_run_count=sum(row.execution_mode == "llm" for row in rows),
        input_tokens=sum(input_values) if input_values else None, output_tokens=sum(output_values) if output_values else None)


def _admin(actor: DemoActor, db: Session, action: str, policy_id: str | None = None, version: str | None = None):
    if not has_capability(actor, "policy_drafts.manage"):
        add_policy_audit_event(db, actor, action, "DENIED", "ADMIN_REQUIRED", policy_id, version)
        db.commit()
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "Only workspace administrators can manage policy drafts."})


def _draft_response(row: PolicyDraft) -> PolicyDraftResponse:
    return PolicyDraftResponse(id=row.id, policy_id=row.policy_id, version=row.version, title=row.title,
        action_type=row.action_type,
        policy_scope=row.policy_scope, scope_product_id=row.scope_product_id,
        customer_tier=row.customer_tier, max_discount_pct=row.max_discount_pct,
        review_max_discount_pct=row.review_max_discount_pct, exception_approval_allowed=row.exception_approval_allowed,
        status=row.status, source=row.source)


def _deny_policy_action(db: Session, actor: DemoActor, action: str, code: str, message: str,
                       status_code: int = 409, policy_id: str | None = None, version: str | None = None):
    add_policy_audit_event(db, actor, action, "DENIED", code, policy_id, version)
    db.commit()
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


@router.post("/policy-drafts", response_model=PolicyDraftResponse)
async def upload_policy_draft(policy_id: str = Form(min_length=1, max_length=80), version: str = Form(min_length=1, max_length=40),
        file: UploadFile = File(...), actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor, db, "policy_draft_uploaded", policy_id, version)
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
    add_policy_audit_event(db, actor, "policy_draft_uploaded", "SUCCESS", policy_id=policy_id, version=version)
    db.commit()
    db.refresh(row)
    return _draft_response(row)


@router.put("/policy-drafts/{draft_id}", response_model=PolicyDraftResponse)
def edit_policy_draft(draft_id: str, body: PolicyDraftUpdate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor, db, "policy_draft_updated")
    row = db.scalar(select(PolicyDraft).where(PolicyDraft.id == draft_id, PolicyDraft.tenant_id == actor.tenant_id))
    if row is None:
        _deny_policy_action(db, actor, "policy_draft_updated", "POLICY_DRAFT_NOT_FOUND", "Policy draft not found.", 404)
    if row.status != "draft":
        _deny_policy_action(db, actor, "policy_draft_updated", "POLICY_DRAFT_LOCKED", "Activated drafts cannot be edited.", policy_id=row.policy_id, version=row.version)
    if body.exception_approval_allowed and (body.review_max_discount_pct is None or body.review_max_discount_pct <= body.max_discount_pct):
        raise HTTPException(status_code=422, detail={"code": "INVALID_REVIEW_LIMIT", "message": "Review limit must exceed the direct discount limit."})
    if not body.exception_approval_allowed and body.review_max_discount_pct is not None:
        raise HTTPException(status_code=422, detail={"code": "UNUSED_REVIEW_LIMIT", "message": "Set the review limit only when manager approval exceptions are enabled."})
    for field in ("title", "action_type", "policy_scope", "scope_product_id", "customer_tier", "max_discount_pct", "review_max_discount_pct", "exception_approval_allowed"):
        setattr(row, field, getattr(body, field))
    add_policy_audit_event(db, actor, "policy_draft_updated", "SUCCESS", policy_id=row.policy_id, version=row.version)
    db.commit()
    db.refresh(row)
    return _draft_response(row)


@router.post("/policy-drafts/{draft_id}/activate", response_model=PolicyDraftResponse)
def activate_draft(draft_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor, db, "policy_activation_attempt", policy_id=None, version=None)
    row = db.scalar(select(PolicyDraft).where(PolicyDraft.id == draft_id, PolicyDraft.tenant_id == actor.tenant_id))
    if row is None:
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_DRAFT_NOT_FOUND", "Policy draft not found.", 404)
    if row.status == "active" and row.policy_row_id:
        return _draft_response(row)
    if row.action_type is None:
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_ACTION_REQUIRED", "Confirm the policy action scope before activation.", policy_id=row.policy_id, version=row.version)
    if row.action_type == "quote_discount" and row.max_discount_pct is None:
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_RULES_INCOMPLETE", "Confirm structured discount rules before activation.", policy_id=row.policy_id, version=row.version)
    if row.action_type == "reference_only" and (row.max_discount_pct is not None or row.review_max_discount_pct is not None or row.exception_approval_allowed):
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_ACTION_MISMATCH", "Reference-only policies cannot carry executable discount rules.", policy_id=row.policy_id, version=row.version)
    if row.policy_scope == "product_specific" and db.scalar(select(Product.id).where(
        Product.id == row.scope_product_id, Product.tenant_id == actor.tenant_id,
    )) is None:
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_SCOPE_PRODUCT_NOT_FOUND", "The scoped product does not exist in this tenant.", policy_id=row.policy_id, version=row.version)
    duplicate = db.scalar(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == row.policy_id, Policy.version == row.version))
    if duplicate:
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_VERSION_EXISTS", "This policy ID and version already exist.", policy_id=row.policy_id, version=row.version)
    siblings = db.scalars(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == row.policy_id)).all()
    if any(item.action_type != row.action_type for item in siblings):
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_ACTION_MISMATCH", "Versions of one policy ID must share an action scope.", policy_id=row.policy_id, version=row.version)
    if any(item.policy_scope != row.policy_scope or item.scope_product_id != row.scope_product_id for item in siblings):
        _deny_policy_action(db, actor, "policy_draft_activated", "POLICY_SCOPE_MISMATCH", "Versions of one policy ID must keep the same applicability scope.", policy_id=row.policy_id, version=row.version)
    policy = Policy(policy_id=row.policy_id, version=row.version, title=row.title, action_type=row.action_type, status="inactive",
        policy_scope=row.policy_scope, scope_product_id=row.scope_product_id,
        customer_tier=row.customer_tier, max_discount_pct=row.max_discount_pct,
        review_max_discount_pct=row.review_max_discount_pct, exception_approval_allowed=row.exception_approval_allowed,
        effective_from=datetime.now(timezone.utc), source=row.source, tenant_id=actor.tenant_id)
    db.add(policy)
    db.flush()
    row.status, row.policy_row_id = "active", policy.id
    activate_policy(db, actor, policy.id, commit=False)
    db.commit()
    db.refresh(row)
    return _draft_response(row)


@router.post("/policy-diffs", response_model=PolicyDiffResponse)
def compare_policies(body: PolicyDiffRequest, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "policy.diff", code="POLICY_DIFF_ROLE_REQUIRED", message="This role cannot compare policy versions.")
    if actor.role == "sales":
        if body.customer_id and body.customer_id != actor.customer_id:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "Customer not found."})
        customer_id = actor.customer_id
    else:
        customer_id = body.customer_id
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id)) if customer_id else None
    if customer is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "Customer not found."})
    try:
        parsed = _parse_intent({"raw_inquiry": body.inquiry, "events": []})
        intent = InquiryIntent.model_validate(parsed["intent"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "POLICY_DIFF_PARSE_FAILED", "message": "The inquiry could not be parsed; no comparison was run."}) from exc
    requested_items = intent.items or ([{"product_id": intent.product_id, "quantity": intent.quantity, "requested_discount_pct": intent.requested_discount_pct}] if intent.product_id else [])
    requested_product_ids = {item["product_id"] if isinstance(item, dict) else item.product_id for item in requested_items}
    products = db.scalars(select(Product).where(Product.id.in_(requested_product_ids), Product.tenant_id == actor.tenant_id)).all() if requested_product_ids else []
    products_by_id = {product.id: product for product in products}
    base = db.scalar(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == body.base_policy_id, Policy.version == body.base_version))
    compare = db.scalar(select(Policy).where(Policy.tenant_id == actor.tenant_id, Policy.policy_id == body.compare_policy_id, Policy.version == body.compare_version))
    if not base or not compare:
        raise HTTPException(status_code=404, detail={"code": "POLICY_VERSION_NOT_FOUND", "message": "Both registered policy versions are required."})
    if base.customer_tier != compare.customer_tier:
        raise HTTPException(status_code=422, detail={"code": "POLICY_SCOPE_MISMATCH", "message": "Policy versions must apply to the same customer tier."})
    if base.action_type != compare.action_type:
        raise HTTPException(status_code=422, detail={"code": "POLICY_ACTION_MISMATCH", "message": "Policy versions must authorize the same action."})
    missing = [] if requested_items else [name for name in ("product_id", "quantity", "requested_discount_pct") if getattr(intent, name) is None]
    simulated_actor = actor if actor.role == "sales" else DemoActor(id=actor.id, display_name=actor.display_name, role="sales", customer_id=customer.id, tenant_id=actor.tenant_id)
    def side(policy: Policy) -> PolicyDiffSide:
        if missing:
            decision = GuardDecision(decision="NEEDS_INPUT", reason_code="MISSING_REQUIRED_FIELDS", policy_id=policy.policy_id,
                                     policy_version=policy.version, human_message="Inquiry is missing: " + ", ".join(missing))
            breakdown = None
            lines = []
        elif customer.tier != policy.customer_tier:
            decision = GuardDecision(decision="DENY", reason_code="POLICY_NOT_APPLICABLE", policy_id=policy.policy_id,
                                     policy_version=policy.version, human_message="This policy does not apply to the selected customer's verified tier.")
            breakdown = None
            lines = []
        elif not requested_items:
            decision = GuardDecision(decision="NEEDS_INPUT", reason_code="MISSING_REQUIRED_FIELDS", policy_id=policy.policy_id,
                                     policy_version=policy.version, human_message="Inquiry must include at least one product, quantity, and discount.")
            breakdown = None
            lines = []
        else:
            all_products_found = all(
                (item["product_id"] if isinstance(item, dict) else item.product_id) in products_by_id
                for item in requested_items
            )
            currencies = {
                products_by_id[item["product_id"] if isinstance(item, dict) else item.product_id].currency
                for item in requested_items
                if (item["product_id"] if isinstance(item, dict) else item.product_id) in products_by_id
            }
            mixed_currency = all_products_found and len(currencies) > 1
            quantities_complete = all((item["quantity"] if isinstance(item, dict) else item.quantity) is not None for item in requested_items)
            inventory_failure = None
            if all_products_found and quantities_complete:
                inventory_failure = aggregate_inventory_failure(
                    (
                        item["product_id"] if isinstance(item, dict) else item.product_id,
                        item["quantity"] if isinstance(item, dict) else item.quantity,
                        products_by_id[item["product_id"] if isinstance(item, dict) else item.product_id].stock,
                    )
                    for item in requested_items
                )
            lines = []
            for item in requested_items:
                product_id = item["product_id"] if isinstance(item, dict) else item.product_id
                quantity = item["quantity"] if isinstance(item, dict) else item.quantity
                discount = item["requested_discount_pct"] if isinstance(item, dict) else item.requested_discount_pct
                product = products_by_id.get(product_id)
                if mixed_currency:
                    line_decision = GuardDecision(decision="DENY", reason_code="MIXED_CURRENCY_UNSUPPORTED", policy_id=policy.policy_id,
                                                  policy_version=policy.version, human_message="All items in one quote must use the same currency.")
                    line_breakdown = None
                elif inventory_failure and product_id == inventory_failure[0]:
                    line_decision = GuardDecision(decision="DENY", reason_code="INSUFFICIENT_STOCK", policy_id=policy.policy_id,
                                                  policy_version=policy.version, human_message="The combined requested quantity exceeds current stock.")
                    line_breakdown = None
                elif product is None:
                    line_decision = GuardDecision(decision="DENY", reason_code="BUSINESS_DATA_NOT_FOUND", policy_id=policy.policy_id, policy_version=policy.version,
                                                  human_message="One or more requested products were not found in this workspace.")
                    line_breakdown = None
                elif quantity is None or discount is None:
                    line_decision = GuardDecision(decision="NEEDS_INPUT", reason_code="MISSING_REQUIRED_FIELDS", policy_id=policy.policy_id,
                                                  policy_version=policy.version, human_message="Inquiry is missing quantity or discount for a requested product.")
                    line_breakdown = None
                else:
                    proposal = ActionProposal(customer_id=customer.id, product_id=product.id, quantity=quantity, requested_discount_pct=discount)
                    line_decision, line_breakdown = evaluate_quote(simulated_actor, customer, product, policy, proposal, intent.policy_scope)
                lines.append(PolicyDiffLine(product_id=product_id, decision=line_decision, breakdown=line_breakdown))
            decisions = [line.decision for line in lines]
            selected = next((item for item in decisions if item.decision == "DENY"), None) or next((item for item in decisions if item.decision == "NEEDS_INPUT"), None) or next((item for item in decisions if item.decision == "REVIEW"), None)
            decision = selected or GuardDecision(decision="ALLOW", reason_code="WITHIN_POLICY", policy_id=policy.policy_id,
                policy_version=policy.version, human_message="Every quote line passed this policy version.")
            decision = decision.model_copy(update={"policy_id": policy.policy_id, "policy_version": policy.version})
            breakdown = lines[0].breakdown if len(lines) == 1 else None
        return PolicyDiffSide(policy_id=policy.policy_id, version=policy.version, title=policy.title,
                              citation=policy.source, decision=decision, breakdown=breakdown, lines=lines)
    return PolicyDiffResponse(intent=intent, base=side(base), compare=side(compare))
