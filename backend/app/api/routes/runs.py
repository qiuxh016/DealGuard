from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
from time import perf_counter, sleep
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.agents.workflow import WorkflowStepFailure, build_workflow
from app.core.config import settings
from app.db.database import get_db
from app.db.models import ApprovalRequest, RunRecord
from app.schemas.deal import ApprovalRequestResponse, InquiryRequest, RunResponse, TraceEvent
from app.services.actors import get_current_actor
from app.services.permissions import has_capability
from app.db.models import DemoActor

router = APIRouter(prefix="/runs", tags=["runs"])
logger = logging.getLogger(__name__)
RUN_LEASE_SECONDS = 60


@router.get("")
def list_runs(
    status: str | None = Query(default=None, max_length=32),
    decision: str | None = Query(default=None, max_length=32),
    policy_version: str | None = Query(default=None, max_length=80),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    if created_from and created_to and created_from > created_to:
        raise HTTPException(status_code=422, detail={"code": "INVALID_TIME_RANGE", "message": "created_from must be earlier than created_to."})
    query = select(RunRecord).where(RunRecord.tenant_id == actor.tenant_id)
    if has_capability(actor, "runs.read_own"):
        query = query.where(RunRecord.actor_id == actor.id)
    elif not has_capability(actor, "runs.read_tenant"):
        raise HTTPException(status_code=403, detail={"code": "RUNS_ROLE_REQUIRED", "message": "This role cannot view run history."})
    rows = db.scalars(query.order_by(RunRecord.created_at.desc())).all()
    filtered = []
    for row in rows:
        result = row.result or {}
        run_decision = (result.get("decision") or {}).get("decision")
        evidence = result.get("policy_evidence") or []
        version = evidence[0].get("version") if evidence else (result.get("decision") or {}).get("policy_version")
        created_at = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
        if status and row.status != status:
            continue
        if decision and run_decision != decision:
            continue
        if policy_version and version != policy_version:
            continue
        if created_from and created_at < created_from:
            continue
        if created_to and created_at > created_to:
            continue
        quote = result.get("quote") or {}
        breakdown = quote.get("breakdown") or {}
        filtered.append({
            "run_id": row.id, "actor_id": row.actor_id, "status": row.status,
            "execution_mode": row.execution_mode, "decision": run_decision,
            "reason_code": (result.get("decision") or {}).get("reason_code"),
            "policy_id": evidence[0].get("policy_id") if evidence else (result.get("decision") or {}).get("policy_id"),
            "policy_version": version, "quote_id": quote.get("id"), "quote_total": breakdown.get("total"),
            "created_at": created_at, "duration_ms": row.duration_ms,
        })
    return {"total": len(filtered), "items": filtered[offset:offset + limit], "limit": limit, "offset": offset}


def _replay_or_claim_run(db: Session, actor_id: str, key: str, request_hash: str) -> tuple[RunResponse | None, RunRecord | None]:
    record = db.scalar(select(RunRecord).where(RunRecord.actor_id == actor_id, RunRecord.idempotency_key == key))
    if record is None:
        return None, None
    if record.request_hash != request_hash:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "This idempotency key was already used for a different inquiry."})
    if record.status != "running":
        return RunResponse(run_id=record.id, **record.result), None

    now = datetime.now(timezone.utc)
    lease = record.lease_expires_at
    if lease is not None and lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    if lease is not None and lease > now:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "A request with this idempotency key is still running. Retry after it completes."})

    lease_filter = RunRecord.lease_expires_at.is_(None) if record.lease_expires_at is None else RunRecord.lease_expires_at == record.lease_expires_at
    claimed = None
    for retry in range(40):
        try:
            claimed = db.execute(
                update(RunRecord)
                .where(
                    RunRecord.id == record.id,
                    RunRecord.status == "running",
                    RunRecord.attempt == record.attempt,
                    lease_filter,
                )
                .values(attempt=record.attempt + 1, lease_expires_at=now + timedelta(seconds=RUN_LEASE_SECONDS))
                .execution_options(synchronize_session=False)
            )
            db.commit()
            break
        except OperationalError as error:
            db.rollback()
            if "locked" not in str(error).lower():
                raise
            if retry == 39:
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "Another request is recovering this inquiry."}) from error
            sleep(0.05)
    if claimed is None:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "Another request is recovering this inquiry."})
    if claimed.rowcount == 1:
        db.expire(record)
        db.refresh(record)
        return None, record

    db.rollback()
    record = db.scalar(select(RunRecord).where(RunRecord.actor_id == actor_id, RunRecord.idempotency_key == key))
    if record is not None and record.status != "running":
        return RunResponse(run_id=record.id, **record.result), None
    raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "Another request claimed this inquiry for recovery."})


@router.post("", response_model=RunResponse)
def create_run(
    body: InquiryRequest,
    actor: DemoActor = Depends(get_current_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    db: Session = Depends(get_db),
) -> RunResponse:
    customer_id = body.customer_id or actor.customer_id
    if customer_id is None:
        raise HTTPException(status_code=422, detail={"code": "CUSTOMER_REQUIRED", "message": "Select a customer before running an inquiry."})
    if actor.customer_id and customer_id != actor.customer_id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "This account cannot quote for the selected customer."})
    request_hash = sha256(f"{customer_id}\0{body.inquiry}".encode("utf-8")).hexdigest()
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise HTTPException(status_code=422, detail={"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency-Key cannot be blank."})
        replay, record = _replay_or_claim_run(db, actor.id, idempotency_key, request_hash)
        if replay is not None:
            return replay
    else:
        record = None

    run_id = record.id if record is not None else str(uuid4())
    started = perf_counter()
    mode = "llm" if settings.llm_api_key and settings.llm_model else "demo"
    if record is None:
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=RUN_LEASE_SECONDS)
        record = RunRecord(
            id=run_id, actor_id=actor.id, tenant_id=actor.tenant_id, inquiry=body.inquiry, status="running", execution_mode=mode, result={},
            idempotency_key=idempotency_key, request_hash=request_hash if idempotency_key is not None else None,
            attempt=1, lease_expires_at=lease_expires_at,
        )
        db.add(record)
        try:
            db.commit()
        except (IntegrityError, OperationalError) as error:
            db.rollback()
            if idempotency_key is None:
                raise
            replay, record = _replay_or_claim_run(db, actor.id, idempotency_key, request_hash)
            if replay is not None:
                return replay
            if record is not None:
                run_id = record.id
            elif isinstance(error, OperationalError) and "locked" in str(error).lower():
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_IN_PROGRESS", "message": "Another request with this idempotency key is starting."}) from error
            if record is None:
                raise
    run_attempt = record.attempt
    initial = {
        "run_id": run_id, "actor": actor, "session": db, "raw_inquiry": body.inquiry, "customer_id": customer_id,
        "execution_mode": mode, "events": [], "status": "completed",
    }
    state = {}
    try:
        state = build_workflow().invoke(initial)
        status = state.get("status", "completed")
        response = RunResponse(
            run_id=run_id, status=status, execution_mode=state.get("execution_mode", mode),
            intent=state.get("intent"), decision=state.get("decision"), quote=state.get("quote"),
            policy_evidence=state.get("evidence", []), trace=state.get("events", []),
            input_tokens=state.get("usage", {}).get("input_tokens"),
            output_tokens=state.get("usage", {}).get("output_tokens"),
        )
        if response.decision and response.decision.decision == "REVIEW" and state.get("proposal"):
            proposal = state["proposal"]
            approval = ApprovalRequest(
                run_id=run_id, applicant_actor_id=actor.id, customer_id=proposal["customer_id"],
                tenant_id=actor.tenant_id,
                product_id=proposal["product_id"], quantity=proposal["quantity"],
                discount_pct=proposal["requested_discount_pct"], policy_id=response.decision.policy_id,
                policy_version=response.decision.policy_version, policy_scope=(state.get("intent") or {}).get("policy_scope") or "general",
                state="PENDING", reason=response.decision.reason_code,
                lines=state.get("line_results") and [{"proposal": r["proposal"], "policy_id": r["decision"]["policy_id"], "policy_version": r["decision"]["policy_version"], "policy_source": r["policy_source"], "policy_provenance": r["policy_provenance"], "available_stock": r["available_stock"], "inventory_checked_at": r["inventory_checked_at"]} for r in state["line_results"]] or None,
            )
            db.add(approval)
            db.flush()
            response.approval_request = ApprovalRequestResponse.model_validate(approval, from_attributes=True)
            response.trace.append(TraceEvent(
                step="request_approval", status="pending", message="Saved a manager approval request; no quote was created.",
                timestamp=datetime.now(timezone.utc), details={"approval_id": approval.id},
            ))
    except Exception as error:
        logger.exception("DealGuard run %s failed", run_id)
        db.rollback()
        record = db.get(RunRecord, run_id)
        reason_code = error.reason_code if isinstance(error, WorkflowStepFailure) else "WORKFLOW_FAILED"
        failure_step = error.step if isinstance(error, WorkflowStepFailure) else "workflow"
        failure_message = {
            "LLM_PROVIDER_UNAVAILABLE": "The configured language model is temporarily unavailable; no quote was created.",
            "LLM_PROVIDER_TIMEOUT": "The configured language model timed out; no quote was created.",
            "LLM_CREDENTIALS_INVALID": "The configured language model credentials were rejected; no quote was created.",
            "LLM_REQUEST_INVALID": "The configured language model rejected the request; no quote was created.",
            "LLM_INVALID_OUTPUT": "The configured language model returned unusable structured data; no quote was created.",
            "LLM_REQUEST_FAILED": "The configured language model request failed; no quote was created.",
        }.get(reason_code, "The configured model or workflow failed; no quote was created.")
        event = TraceEvent(
            step=failure_step, status="failed",
            message=failure_message,
            timestamp=datetime.now(timezone.utc), details={"reason_code": reason_code},
        )
        response = RunResponse(
            run_id=run_id, status="failed", execution_mode=mode,
            decision={"decision": "DENY", "reason_code": reason_code, "human_message": event.message},
            trace=[event],
        )
    response.duration_ms = round((perf_counter() - started) * 1000)
    if response.trace:
        response.trace[-1].details.setdefault("end_to_end_duration_ms", response.duration_ms)
    stored_result = response.model_dump(mode="json", exclude={"run_id"})
    if state.get("proposal") and state.get("customer"):
        customer = state["customer"]
        actor_snapshot = state["actor"]
        line_rows = state.get("line_results") or [{
            "proposal": state["proposal"],
            "product_name": getattr(state.get("product"), "name", None),
            "currency": getattr(state.get("product"), "currency", None),
            "unit_price": str(getattr(state.get("product"), "unit_price", "")),
            "available_stock": getattr(state.get("product"), "stock", None),
        }]
        policy = state.get("policy")
        stored_result["_impact_snapshot"] = {
            "actor": {"id": actor_snapshot.id, "role": actor_snapshot.role, "customer_id": actor_snapshot.customer_id},
            "customer": {"id": customer.id, "tier": customer.tier},
            "tenant_id": actor_snapshot.tenant_id,
            "policy_scope": (state.get("intent") or {}).get("policy_scope") or "general",
            "lines": [
                {
                    "proposal": row.get("proposal"),
                    "product": {
                        "id": (row.get("proposal") or {}).get("product_id"),
                        "name": row.get("product_name"),
                        "currency": row.get("currency"),
                        "unit_price": row.get("unit_price"),
                        "stock": row.get("available_stock"),
                    },
                }
                for row in line_rows
            ],
            "policy": ({
                "policy_id": policy.policy_id, "version": policy.version, "title": policy.title,
                "action_type": policy.action_type, "policy_scope": policy.policy_scope,
                "scope_product_id": policy.scope_product_id, "customer_tier": policy.customer_tier,
                "max_discount_pct": str(policy.max_discount_pct) if policy.max_discount_pct is not None else None,
                "review_max_discount_pct": str(policy.review_max_discount_pct) if policy.review_max_discount_pct is not None else None,
                "exception_approval_allowed": policy.exception_approval_allowed,
                "effective_from": policy.effective_from.isoformat(),
                "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
                "source": policy.source,
            } if policy else None),
        }
    finalized = db.execute(
        update(RunRecord)
        .where(RunRecord.id == run_id, RunRecord.status == "running", RunRecord.attempt == run_attempt)
        .values(
            duration_ms=response.duration_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            status=response.status,
            execution_mode=response.execution_mode,
            result=stored_result,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if finalized.rowcount != 1:
        db.rollback()
        current = db.get(RunRecord, run_id)
        if current is not None and current.status != "running":
            return RunResponse(run_id=current.id, **current.result)
        raise HTTPException(status_code=409, detail={"code": "RUN_ATTEMPT_SUPERSEDED", "message": "A newer attempt claimed this run; retry with the same idempotency key."})
    db.commit()
    return response


@router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: str,
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> RunResponse:
    record = db.scalar(select(RunRecord).where(RunRecord.id == run_id, RunRecord.tenant_id == actor.tenant_id))
    if record is None or (not has_capability(actor, "runs.read_all") and (not has_capability(actor, "runs.read_own") or record.actor_id != actor.id)):
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "Run not found."})
    return RunResponse(run_id=record.id, **record.result)
