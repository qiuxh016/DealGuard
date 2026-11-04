import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.workflow import _parse_intent
from app.db.database import get_db
from app.db.models import Customer, DemoActor, Policy, PolicyDraft, Product
from app.guards.business_rules import evaluate_quote
from app.schemas.deal import ActionProposal, GuardDecision, InquiryIntent, MetricsResponse, PolicyDiffLine, PolicyDiffRequest, PolicyDiffResponse, PolicyDiffSide, PolicyDraftCreate, PolicyDraftResponse, PolicyDraftSummary, PolicyDraftUpdate
from app.db.models import PolicyAuditEvent, RunRecord
from app.services.actors import get_current_actor
from app.services.permissions import has_capability, require_capability
from app.services.inventory import order_constraint_failure
from app.services.policy_audit import add_policy_audit_event
from app.services.policy_extraction import extract_policy_rules
from app.services.policy_import import MAX_POLICY_IMPORT_BYTES, PolicyImportError, parse_policy_import, policy_import_template, public_policy_import_preview
from app.services.policy_versions import reserve_policy_version
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


def _draft_summary(row: PolicyDraft) -> PolicyDraftSummary:
    return PolicyDraftSummary(
        id=row.id, policy_id=row.policy_id, version=row.version, title=row.title,
        action_type=row.action_type, policy_scope=row.policy_scope, scope_product_id=row.scope_product_id,
        customer_tier=row.customer_tier, max_discount_pct=row.max_discount_pct,
        review_max_discount_pct=row.review_max_discount_pct,
        exception_approval_allowed=row.exception_approval_allowed, status=row.status,
    )


def _deny_policy_action(db: Session, actor: DemoActor, action: str, code: str, message: str,
                       status_code: int = 409, policy_id: str | None = None, version: str | None = None):
    add_policy_audit_event(db, actor, action, "DENIED", code, policy_id, version)
    db.commit()
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _reserve_policy_version(db: Session, actor: DemoActor, policy_id: str, version: str):
    if not reserve_policy_version(db, actor.tenant_id, policy_id, version):
        raise HTTPException(status_code=409, detail={
            "code": "POLICY_VERSION_EXISTS",
            "message": "This policy ID and version already exist in this workspace.",
        })


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
    _reserve_policy_version(db, actor, policy_id, version)
    row = PolicyDraft(policy_id=policy_id, version=version, source=source, uploaded_by=actor.id, tenant_id=actor.tenant_id)
    db.add(row)
    add_policy_audit_event(db, actor, "policy_draft_uploaded", "SUCCESS", policy_id=policy_id, version=version)
    db.commit()
    db.refresh(row)
    return _draft_response(row)


@router.get("/policy-drafts", response_model=list[PolicyDraftSummary])
def list_policy_drafts(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    if not has_capability(actor, "policy_drafts.manage"):
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "Only workspace administrators can view policy drafts."})
    rows = db.scalars(select(PolicyDraft).where(
        PolicyDraft.tenant_id == actor.tenant_id, PolicyDraft.status == "draft",
    ).order_by(PolicyDraft.created_at.desc()).limit(100)).all()
    return [_draft_summary(row) for row in rows]


@router.post("/policy-drafts/extract")
async def extract_policy_draft_rules(
    policy_id: str = Form(min_length=2, max_length=80),
    version: str = Form(min_length=1, max_length=40),
    file: UploadFile = File(...),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    _admin(actor, db, "policy_rules_extraction_requested", policy_id, version)
    if Path(file.filename or "").suffix.lower() not in {".md", ".txt"}:
        raise HTTPException(status_code=415, detail={"code": "UNSUPPORTED_POLICY_FILE", "message": "请上传 UTF-8 编码的 .md 或 .txt 文件。"})
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"code": "POLICY_FILE_TOO_LARGE", "message": "政策文件不能超过 1 MiB。"})
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail={"code": "INVALID_POLICY_ENCODING", "message": "政策文件必须使用 UTF-8 编码。"}) from error
    if not source.strip():
        raise HTTPException(status_code=422, detail={"code": "EMPTY_POLICY_FILE", "message": "政策原文不能为空。"})
    try:
        extracted = await asyncio.to_thread(extract_policy_rules, source)
    except RuntimeError as error:
        code = str(error)
        message = "请先配置 OpenAI-compatible 模型 API 后再使用规则识别。" if code == "POLICY_EXTRACTION_MODEL_NOT_CONFIGURED" else "政策文本超过模型识别长度上限（50,000 字符）。"
        add_policy_audit_event(db, actor, "policy_rules_extraction_failed", "FAILED", code, policy_id, version)
        db.commit()
        raise HTTPException(status_code=503 if code == "POLICY_EXTRACTION_MODEL_NOT_CONFIGURED" else 413,
                            detail={"code": code, "message": message}) from error
    except Exception as error:
        add_policy_audit_event(db, actor, "policy_rules_extraction_failed", "FAILED", "POLICY_EXTRACTION_FAILED", policy_id, version)
        db.commit()
        raise HTTPException(status_code=503, detail={"code": "POLICY_EXTRACTION_FAILED", "message": "模型未能可靠识别政策结构，请手动填写规则或检查模型配置。"}) from error

    add_policy_audit_event(db, actor, "policy_rules_extracted", "SUCCESS", policy_id=policy_id, version=version)
    db.commit()
    return {"filename": file.filename, "extraction_mode": "llm", "fields": extracted.model_dump(mode="json"), "source_characters": len(source)}


@router.post("/policy-drafts/confirmed", response_model=PolicyDraftResponse, status_code=201)
def create_confirmed_policy_draft(body: PolicyDraftCreate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor, db, "policy_draft_confirmed", body.policy_id, body.version)
    _reserve_policy_version(db, actor, body.policy_id, body.version)
    if body.policy_scope == "product_specific" and db.scalar(select(Product.id).where(
        Product.id == body.scope_product_id, Product.tenant_id == actor.tenant_id,
    )) is None:
        raise HTTPException(status_code=422, detail={"code": "POLICY_SCOPE_PRODUCT_NOT_FOUND", "message": "指定商品不在当前工作区。"})
    row = PolicyDraft(
        policy_id=body.policy_id, version=body.version, title=body.title, source=body.source,
        action_type=body.action_type, policy_scope=body.policy_scope, scope_product_id=body.scope_product_id,
        customer_tier=body.customer_tier, max_discount_pct=body.max_discount_pct,
        review_max_discount_pct=body.review_max_discount_pct,
        exception_approval_allowed=body.exception_approval_allowed, uploaded_by=actor.id, tenant_id=actor.tenant_id,
    )
    db.add(row)
    add_policy_audit_event(db, actor, "policy_draft_created_from_review", "SUCCESS", policy_id=body.policy_id, version=body.version)
    db.commit()
    db.refresh(row)
    return _draft_response(row)


def _policy_import_context(file: UploadFile, content: bytes, actor: DemoActor, db: Session):
    try:
        rows = parse_policy_import(content, file.filename or "")
    except PolicyImportError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": "POLICY_IMPORT_FILE_INVALID", "message": str(error)}) from error
    existing = {(policy_id.upper(), version.upper()) for policy_id, version in db.execute(
        select(Policy.policy_id, Policy.version).where(Policy.tenant_id == actor.tenant_id)
    ).all()}
    existing.update((policy_id.upper(), version.upper()) for policy_id, version in db.execute(
        select(PolicyDraft.policy_id, PolicyDraft.version).where(PolicyDraft.tenant_id == actor.tenant_id)
    ).all())
    preview = public_policy_import_preview(rows, existing)
    product_ids = {row["draft"]["scope_product_id"] for row in rows if row["draft"] and row["draft"]["policy_scope"] == "product_specific"}
    available_products = set(db.scalars(select(Product.id).where(
        Product.tenant_id == actor.tenant_id, Product.id.in_(product_ids or {""}),
    )).all())
    for row, public_row in zip(rows, preview["rows"], strict=True):
        if row["draft"] and row["draft"]["policy_scope"] == "product_specific" and row["draft"]["scope_product_id"] not in available_products:
            public_row["errors"].append("指定商品不在当前工作区。")
    preview["valid_rows"] = sum(not row["errors"] for row in preview["rows"])
    preview["invalid_rows"] = preview["total_rows"] - preview["valid_rows"]
    return rows, preview


@router.get("/policy-drafts/import/template")
def download_policy_import_template(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    _admin(actor, db, "policy_import_template_downloaded")
    content, media_type, filename = policy_import_template(format)
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/policy-drafts/import/preview")
async def preview_policy_import(file: UploadFile = File(...), actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor, db, "policy_import_previewed")
    content = await file.read(MAX_POLICY_IMPORT_BYTES + 1)
    if len(content) > MAX_POLICY_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail={"code": "POLICY_IMPORT_TOO_LARGE", "message": "文件不能超过 5 MiB。"})
    _, preview = _policy_import_context(file, content, actor, db)
    return {"filename": file.filename, **preview}


@router.post("/policy-drafts/import")
async def import_policy_drafts(file: UploadFile = File(...), actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    _admin(actor, db, "policy_drafts_imported")
    content = await file.read(MAX_POLICY_IMPORT_BYTES + 1)
    if len(content) > MAX_POLICY_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail={"code": "POLICY_IMPORT_TOO_LARGE", "message": "文件不能超过 5 MiB。"})
    rows, preview = _policy_import_context(file, content, actor, db)
    if preview["total_rows"] == 0:
        raise HTTPException(status_code=422, detail={"code": "POLICY_IMPORT_EMPTY", "message": "文件中没有政策数据行。"})
    if preview["invalid_rows"]:
        raise HTTPException(status_code=422, detail={
            "code": "POLICY_IMPORT_VALIDATION_FAILED", "message": "发现无效或重复行；整批没有导入。请修正文件后重新预览。",
            "preview": {"filename": file.filename, **preview},
        })
    for row in rows:
        draft_data = row["draft"]
        _reserve_policy_version(db, actor, draft_data["policy_id"], draft_data["version"])
    drafts = [PolicyDraft(**row["draft"], uploaded_by=actor.id, tenant_id=actor.tenant_id) for row in rows]
    db.add_all(drafts)
    for row in rows:
        add_policy_audit_event(db, actor, "policy_draft_imported", "SUCCESS", policy_id=row["draft"]["policy_id"], version=row["draft"]["version"])
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "POLICY_IMPORT_CONFLICT", "message": "导入过程中数据发生变化；整批没有导入，请重新预览。"}) from error
    for draft in drafts:
        db.refresh(draft)
    return {"imported_count": len(drafts), "drafts": [_draft_response(draft).model_dump(mode="json") for draft in drafts]}


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
            quantities_complete = all((item["quantity"] if isinstance(item, dict) else item.quantity) is not None for item in requested_items)
            constraint_failure = None
            if all_products_found and quantities_complete:
                constraint_failure = order_constraint_failure(
                    (
                        item["product_id"] if isinstance(item, dict) else item.product_id,
                        products_by_id[item["product_id"] if isinstance(item, dict) else item.product_id].currency,
                        item["quantity"] if isinstance(item, dict) else item.quantity,
                        products_by_id[item["product_id"] if isinstance(item, dict) else item.product_id].stock,
                    ) for item in requested_items
                )
            lines = []
            for item in requested_items:
                product_id = item["product_id"] if isinstance(item, dict) else item.product_id
                quantity = item["quantity"] if isinstance(item, dict) else item.quantity
                discount = item["requested_discount_pct"] if isinstance(item, dict) else item.requested_discount_pct
                product = products_by_id.get(product_id)
                if constraint_failure and constraint_failure[0] == "MIXED_CURRENCY_UNSUPPORTED":
                    line_decision = GuardDecision(decision="DENY", reason_code=constraint_failure[0], policy_id=policy.policy_id,
                                                  policy_version=policy.version, human_message="All items in one quote must use the same currency.")
                    line_breakdown = None
                elif constraint_failure and constraint_failure[0] == "INSUFFICIENT_STOCK" and product_id == constraint_failure[1]:
                    line_decision = GuardDecision(decision="DENY", reason_code=constraint_failure[0], policy_id=policy.policy_id,
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
