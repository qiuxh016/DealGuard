from datetime import datetime, timezone
from decimal import Decimal
from time import sleep

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models import ApprovalRequest, Customer, DemoActor, Product, QuoteDraft, RunRecord
from app.guards.business_rules import calculate_breakdown, evaluate_quote_with_trace
from app.rag.provenance import policy_source_hash, quote_provenance
from app.schemas.deal import ActionProposal, ApprovalOutcome, ApprovalRequestResponse, QuoteBreakdown, TraceEvent
from app.services.quotes import quote_response
from app.services.permissions import has_capability
from app.services.policies import current_quote_policies
from app.services.inventory import aggregate_inventory_failure


def _manager(actor: DemoActor) -> None:
    if not has_capability(actor, "approvals.decide"):
        raise HTTPException(status_code=403, detail={"code": "MANAGER_REQUIRED", "message": "Only workspace managers can process approvals."})


def _trace(db: Session, row: ApprovalRequest, status: str, message: str, code: str) -> list[TraceEvent]:
    record = db.get(RunRecord, row.run_id)
    trace = list((record.result or {}).get("trace", [])) if record else []
    trace.append(TraceEvent(step="manager_approval", status=status, message=message,
                            timestamp=datetime.now(timezone.utc), details={"approval_id": row.id, "reason_code": code}).model_dump(mode="json"))
    if record:
        record.result = {**record.result, "trace": trace}
    return [TraceEvent.model_validate(event) for event in trace]


def _transition_pending(db: Session, row: ApprovalRequest, state: str, actor_id: str, reason: str | None = None) -> bool:
    resolved_at = datetime.now(timezone.utc)
    result = None
    for attempt in range(40):
        try:
            result = db.execute(
                update(ApprovalRequest)
                .where(ApprovalRequest.id == row.id, ApprovalRequest.state == "PENDING")
                .values(state=state, reason=reason, resolved_by=actor_id, resolved_at=resolved_at)
                .execution_options(synchronize_session=False)
            )
            break
        except OperationalError as error:
            db.rollback()
            if "locked" not in str(error).lower():
                raise
            if attempt == 39:
                return False
            sleep(0.05)
    if result is None:
        return False
    if result.rowcount != 1:
        return False
    row.state = state
    row.reason = reason
    row.resolved_by = actor_id
    row.resolved_at = resolved_at
    return True


def _approval_conflict(db: Session, approval_id: str, approve: bool) -> ApprovalOutcome:
    db.rollback()
    row = db.get(ApprovalRequest, approval_id)
    if approve and row is not None and row.state == "APPROVED":
        quote = db.get(QuoteDraft, row.quote_id) if row.quote_id else None
        return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), quote=quote_response(quote) if quote else None)
    if row is not None and row.state == "PENDING":
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_IN_PROGRESS", "message": "Another manager request is still processing this approval."})
    current_state = row.state.lower() if row is not None else "missing"
    raise HTTPException(status_code=409, detail={"code": "APPROVAL_NOT_PENDING", "message": f"Approval is already {current_state}."})


def list_pending(db: Session, tenant_id: str) -> list[ApprovalRequestResponse]:
    rows = db.scalars(select(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.state == "PENDING").order_by(ApprovalRequest.created_at)).all()
    return [ApprovalRequestResponse.model_validate(row, from_attributes=True) for row in rows]


def process_approval(db: Session, actor: DemoActor, approval_id: str, approve: bool, reason: str | None = None) -> ApprovalOutcome:
    _manager(actor)
    row = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "APPROVAL_NOT_FOUND", "message": "Approval request not found."})
    if row.applicant_actor_id == actor.id:
        raise HTTPException(status_code=403, detail={"code": "SELF_APPROVAL_FORBIDDEN", "message": "Applicants cannot approve their own requests."})
    if row.state == "APPROVED":
        quote = db.get(QuoteDraft, row.quote_id) if row.quote_id else None
        trace = _trace(db, row, "completed", "Approval was already completed.", "APPROVAL_ALREADY_COMPLETED")
        db.commit()
        return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), quote=quote_response(quote) if quote else None, trace=trace)
    if row.state != "PENDING":
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_NOT_PENDING", "message": f"Approval is already {row.state.lower()}."})
    if not approve:
        if not _transition_pending(db, row, "REJECTED", actor.id, reason.strip()):
            return _approval_conflict(db, approval_id, approve)
        trace = _trace(db, row, "rejected", "Manager rejected the request. No quote was created.", "APPROVAL_REJECTED")
        db.commit()
        db.refresh(row)
        return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), trace=trace)

    applicant = db.get(DemoActor, row.applicant_actor_id)
    customer = db.scalar(select(Customer).where(Customer.id == row.customer_id, Customer.tenant_id == actor.tenant_id))
    if row.lines:
        current_lines = []
        current_product_keys: dict[str, str] = {}
        stale_code = None
        now = datetime.now(timezone.utc)
        if applicant is None or not has_capability(applicant, "inquiry.run") or applicant.customer_id != row.customer_id or customer is None:
            stale_code = "APPROVAL_CONTEXT_CHANGED"
        for saved in row.lines:
            if stale_code:
                break
            proposal_data = saved["proposal"]
            product = db.scalar(select(Product).where(Product.id == proposal_data["product_id"], Product.tenant_id == actor.tenant_id))
            policies = current_quote_policies(db, actor.tenant_id, customer.tier, saved["policy_id"], now)
            current = policies[0] if len(policies) == 1 else None
            if product is None:
                stale_code = "APPROVAL_CONTEXT_CHANGED"
            elif current is None or current.version != saved["policy_version"]:
                stale_code = "POLICY_CHANGED"
            elif saved.get("policy_provenance", {}).get("policy_source_hash") and saved["policy_provenance"]["policy_source_hash"] != policy_source_hash(current):
                stale_code = "POLICY_SOURCE_CHANGED"
            else:
                proposal = ActionProposal(**proposal_data)
                decision, breakdown, guard_checks = evaluate_quote_with_trace(applicant, customer, product, current, proposal, row.policy_scope)
                if decision.decision not in {"ALLOW", "REVIEW"}:
                    stale_code = decision.reason_code
                    break
                elif breakdown is None:
                    breakdown = calculate_breakdown(product, proposal.quantity, proposal.requested_discount_pct)
                current_product_keys[product.sku] = product.internal_id
                provenance = quote_provenance(current, decision, guard_checks, saved.get("policy_provenance", {}).get("citation"), saved.get("policy_provenance", {}).get("citation_ref"), saved.get("policy_provenance", {}).get("chunk_id"))
                current_lines.append({"product_id": product.id, "product_name": product.name, "currency": product.currency, "quantity": proposal.quantity, "unit_price": str(product.unit_price), "discount_pct": str(proposal.requested_discount_pct), "subtotal": str(breakdown.subtotal), "discount_amount": str(breakdown.discount_amount), "total": str(breakdown.total), "policy_id": current.policy_id, "policy_version": current.version, "policy_source": current.source, "policy_scope": current.policy_scope, "available_stock": product.stock, "inventory_checked_at": now.isoformat(), **provenance})
        if not stale_code:
            inventory_failure = aggregate_inventory_failure(
                (line["product_id"], line["quantity"], line["available_stock"])
                for line in current_lines
            )
            if inventory_failure:
                stale_code = "INSUFFICIENT_STOCK"
        if stale_code:
            if not _transition_pending(db, row, "STALE", actor.id, stale_code):
                return _approval_conflict(db, approval_id, approve)
            trace = _trace(db, row, "stale", "Business context changed; the request is stale and no quote was created.", stale_code)
            db.commit(); db.refresh(row)
            return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), trace=trace)
        if not _transition_pending(db, row, "APPROVED", actor.id):
            return _approval_conflict(db, approval_id, approve)
        first = current_lines[0]
        subtotal = sum((Decimal(line["subtotal"]) for line in current_lines), Decimal("0"))
        discount_amount = sum((Decimal(line["discount_amount"]) for line in current_lines), Decimal("0"))
        total = sum((Decimal(line["total"]) for line in current_lines), Decimal("0"))
        quote = QuoteDraft(tenant_id=actor.tenant_id, owner_actor_id=applicant.id, customer_id=customer.id, product_key=current_product_keys[first["product_id"]], product_id=first["product_id"], quantity=sum(line["quantity"] for line in current_lines), unit_price=Decimal(first["unit_price"]), discount_pct=Decimal(first["discount_pct"]), subtotal=subtotal, discount_amount=discount_amount, total=total, policy_id=first["policy_id"], policy_version=first["policy_version"], policy_scope=row.policy_scope, lines=current_lines, status="DRAFT")
        db.add(quote); db.flush(); row.quote_id = quote.id
        trace = _trace(db, row, "approved", "Manager approved after revalidating every line.", "APPROVAL_APPROVED")
        db.commit(); db.refresh(row); db.refresh(quote)
        return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), quote=quote_response(quote), trace=trace)
    product = db.scalar(select(Product).where(Product.id == row.product_id, Product.tenant_id == actor.tenant_id))
    now = datetime.now(timezone.utc)
    policies = current_quote_policies(db, actor.tenant_id, customer.tier, row.policy_id, now) if customer else []
    current = policies[0] if len(policies) == 1 else None
    stale_code = None
    if applicant is None or not has_capability(applicant, "inquiry.run") or applicant.customer_id != row.customer_id or customer is None or product is None:
        stale_code = "APPROVAL_CONTEXT_CHANGED"
    elif current is None or current.version != row.policy_version:
        stale_code = "POLICY_CHANGED"
    else:
        proposal = ActionProposal(customer_id=row.customer_id, product_id=row.product_id, quantity=row.quantity, requested_discount_pct=row.discount_pct)
        decision, breakdown, guard_checks = evaluate_quote_with_trace(applicant, customer, product, current, proposal, row.policy_scope)
        if decision.decision not in {"ALLOW", "REVIEW"}:
            stale_code = decision.reason_code
        elif breakdown is None:
            breakdown = calculate_breakdown(product, row.quantity, row.discount_pct)

    if stale_code:
        if not _transition_pending(db, row, "STALE", actor.id, stale_code):
            return _approval_conflict(db, approval_id, approve)
        trace = _trace(db, row, "stale", "Business context changed; the request is stale and no quote was created.", stale_code)
        db.commit()
        db.refresh(row)
        return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), trace=trace)

    if not _transition_pending(db, row, "APPROVED", actor.id):
        return _approval_conflict(db, approval_id, approve)
    line = {"product_id": product.id, "product_name": product.name, "currency": product.currency, "quantity": row.quantity, "unit_price": str(product.unit_price), "discount_pct": str(row.discount_pct), "subtotal": str(breakdown.subtotal), "discount_amount": str(breakdown.discount_amount), "total": str(breakdown.total), "policy_id": current.policy_id, "policy_version": current.version, "policy_source": current.source, "policy_scope": row.policy_scope, "available_stock": product.stock, "inventory_checked_at": now.isoformat(), **quote_provenance(current, decision, guard_checks, None, None, None)}
    quote = QuoteDraft(
        tenant_id=actor.tenant_id,
        owner_actor_id=applicant.id, customer_id=customer.id, product_key=product.internal_id, product_id=product.sku,
        quantity=row.quantity, unit_price=product.unit_price, discount_pct=row.discount_pct,
        subtotal=breakdown.subtotal, discount_amount=breakdown.discount_amount, total=breakdown.total,
        policy_id=current.policy_id, policy_version=current.version, status="DRAFT",
        policy_scope=row.policy_scope, lines=[line],
    )
    db.add(quote)
    db.flush()
    row.quote_id = quote.id
    trace = _trace(db, row, "approved", "Manager approved after revalidation and created one quote draft.", "APPROVAL_APPROVED")
    db.commit()
    db.refresh(row)
    db.refresh(quote)
    return ApprovalOutcome(approval=ApprovalRequestResponse.model_validate(row, from_attributes=True), quote=quote_response(quote), trace=trace)
