from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ApprovalRequest, Customer, DemoActor, Policy, Product, QuoteAccessToken, QuoteDraft, QuoteRevision, QuoteStatusEvent, RunRecord
from app.guards.business_rules import calculate_breakdown, evaluate_quote_with_trace, policy_scope_applies
from app.rag.provenance import policy_source_hash, quote_provenance
from app.schemas.deal import ActionProposal, QuoteChangeLine
from app.services.inventory import order_constraint_failure
from app.services.policies import current_quote_policies

PUBLIC_QUOTE_TTL = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _add_event(db: Session, quote: QuoteDraft, *, old: str | None, new: str, actor: DemoActor | None,
               reason: str, revision_id: str | None = None, key: str | None = None, details: dict | None = None) -> QuoteStatusEvent:
    event = QuoteStatusEvent(
        tenant_id=quote.tenant_id, quote_id=quote.id, revision_id=revision_id,
        from_status=old, to_status=new, actor_id=actor.id if actor else None,
        actor_type="customer" if actor is None else actor.role,
        reason_code=reason, idempotency_key=key, details=details or {},
    )
    db.add(event)
    return event


def _stored_lines(quote: QuoteDraft) -> list[dict]:
    if quote.lines:
        return [dict(line) for line in quote.lines]
    return [{
        "product_id": quote.product_id, "product_name": quote.product_id, "currency": "USD",
        "quantity": quote.quantity, "unit_price": str(quote.unit_price),
        "discount_pct": str(quote.discount_pct), "subtotal": str(quote.subtotal),
        "discount_amount": str(quote.discount_amount), "total": str(quote.total),
        "policy_id": quote.policy_id, "policy_version": quote.policy_version,
        "policy_scope": quote.policy_scope,
    }]


def _catalog_context(db: Session, actor: DemoActor, customer: Customer, product_id: str, scope: str,
                     policy_id: str | None = None):
    product = db.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == actor.tenant_id))
    if product is None:
        raise HTTPException(status_code=409, detail={"code": "BUSINESS_DATA_NOT_FOUND", "message": f"Product {product_id} is no longer available."})
    candidates = [policy for policy in current_quote_policies(db, actor.tenant_id, customer.tier, policy_id)
                  if policy_scope_applies(policy, scope, product)]
    if len(candidates) != 1:
        raise HTTPException(status_code=409, detail={"code": "POLICY_CONTEXT_CHANGED", "message": f"There is no unique active policy for {product_id}. Review policy scope before revising this quote."})
    return product, candidates[0]


def _evaluate_lines(db: Session, actor: DemoActor, customer: Customer, requested: list[QuoteChangeLine],
                    previous: list[dict], *, allow_review: bool = False) -> tuple[list[dict], list[dict]]:
    old_by_product: dict[str, dict] = {}
    for line in previous:
        old_by_product.setdefault(line["product_id"], line)
    products: list[Product] = []
    policies: list[Policy] = []
    scopes: list[str] = []
    for item in requested:
        old_line = old_by_product.get(item.product_id, {})
        scope = old_line.get("policy_scope", "general")
        product, policy = _catalog_context(db, actor, customer, item.product_id, scope, old_line.get("policy_id"))
        products.append(product); policies.append(policy); scopes.append(scope)

    failure = order_constraint_failure([(product.id, product.currency, item.quantity, product.stock) for item, product in zip(requested, products)])
    if failure:
        code, product_id, quantity, stock = failure
        raise HTTPException(status_code=409, detail={"code": code, "message": f"Quote cannot be revised: {product_id or 'currency'}; requested {quantity}, available {stock}."})

    new_lines: list[dict] = []
    evaluations: list[dict] = []
    now = _now().isoformat()
    for item, product, policy, scope in zip(requested, products, policies, scopes):
        proposal = ActionProposal(customer_id=customer.id, product_id=product.id, quantity=item.quantity,
                                  requested_discount_pct=item.requested_discount_pct)
        decision, breakdown, checks = evaluate_quote_with_trace(actor, customer, product, policy, proposal, scope)
        evaluations.append({"product_id": product.id, "decision": decision.model_dump(mode="json"), "checks": checks})
        if allow_review and decision.decision == "REVIEW" and breakdown is None:
            breakdown = calculate_breakdown(product, proposal.quantity, proposal.requested_discount_pct)
        if decision.decision not in ({"ALLOW", "REVIEW"} if allow_review else {"ALLOW"}) or breakdown is None:
            raise HTTPException(status_code=409, detail={"code": decision.reason_code, "message": decision.human_message, "product_id": product.id})
        old_line = old_by_product.get(item.product_id, {})
        same_policy_source = (old_line.get("policy_source_hash") == policy_source_hash(policy)
                              and old_line.get("policy_id") == policy.policy_id
                              and old_line.get("policy_version") == policy.version)
        citation = old_line.get("citation") if same_policy_source else None
        citation_ref = old_line.get("citation_ref") if same_policy_source else None
        chunk_id = old_line.get("chunk_id") if same_policy_source else None
        line = {
            **(old_line if same_policy_source else {}),
            "product_id": product.id, "product_key": product.internal_id,
            "product_name": product.name, "currency": product.currency,
            "quantity": item.quantity, "unit_price": str(breakdown.unit_price),
            "discount_pct": str(breakdown.discount_pct), "subtotal": str(breakdown.subtotal),
            "discount_amount": str(breakdown.discount_amount), "total": str(breakdown.total),
            "policy_id": policy.policy_id, "policy_version": policy.version,
            "policy_source": policy.source, "policy_scope": scope,
            "available_stock": product.stock, "inventory_checked_at": now,
        }
        line.update(quote_provenance(policy, decision, checks, citation, citation_ref, chunk_id))
        new_lines.append(line)
    return new_lines, evaluations


def _matches_approved_review(db: Session, quote: QuoteDraft, lines: list[dict], evaluations: list[dict]) -> bool:
    if not any(item["decision"]["decision"] == "REVIEW" for item in evaluations):
        return True
    approvals = db.scalars(select(ApprovalRequest).where(
        ApprovalRequest.quote_id == quote.id, ApprovalRequest.tenant_id == quote.tenant_id,
        ApprovalRequest.state == "APPROVED",
    )).all()
    for approval in approvals:
        saved_lines = approval.lines or []
        if len(saved_lines) != len(lines) or approval.resolved_by is None:
            continue
        matches = True
        for saved, line in zip(saved_lines, lines):
            proposal = saved.get("proposal", {})
            provenance = saved.get("policy_provenance", {})
            if (proposal.get("product_id") != line["product_id"]
                    or int(proposal.get("quantity", 0)) != int(line["quantity"])
                    or Decimal(str(proposal.get("requested_discount_pct", "-1"))) != Decimal(line["discount_pct"])
                    or saved.get("policy_id") != line["policy_id"]
                    or saved.get("policy_version") != line["policy_version"]
                    or provenance.get("policy_source_hash") != line.get("policy_source_hash")):
                matches = False
                break
        if matches:
            return True
    return False


def _set_quote_totals(quote: QuoteDraft, lines: list[dict]) -> None:
    quote.lines = lines
    quote.product_id = lines[0]["product_id"]
    quote.product_key = lines[0]["product_key"]
    quote.quantity = sum(line["quantity"] for line in lines)
    quote.unit_price = Decimal(lines[0]["unit_price"])
    quote.discount_pct = Decimal(lines[0]["discount_pct"])
    quote.subtotal = sum((Decimal(line["subtotal"]) for line in lines), Decimal("0.00"))
    quote.discount_amount = sum((Decimal(line["discount_amount"]) for line in lines), Decimal("0.00"))
    quote.total = sum((Decimal(line["total"]) for line in lines), Decimal("0.00"))
    quote.policy_id = lines[0]["policy_id"]
    quote.policy_version = lines[0]["policy_version"]
    quote.policy_scope = lines[0].get("policy_scope", "general")


def plan_counteroffer(db: Session, actor: DemoActor, quote: QuoteDraft, customer: Customer,
                      requested_discount_pct: Decimal, quantity: int | None = None) -> dict:
    if quote.status not in {"DRAFT", "REVISED", "ISSUED", "REJECTED", "EXPIRED"}:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_STATE_CONFLICT", "message": "This quote cannot receive counteroffers in its current state."})
    lines = _stored_lines(quote)
    if quantity is not None and len(lines) != 1:
        raise HTTPException(status_code=422, detail={"code": "COUNTEROFFER_QUANTITY_MULTI_ITEM", "message": "Set a revised quantity per product by creating a new inquiry. This preview accepts one quantity for single-item quotes."})
    request_items: list[dict] = []
    alternative_items: list[dict] = []
    alternative_products: list[Product] = []
    requested_products: list[Product] = []
    requested_quote_lines: list[dict] = []
    requested_evaluations: list[dict] = []
    review_required = False
    all_allowed = True
    all_review_eligible = True
    for line in lines:
        product, policy = _catalog_context(db, actor, customer, line["product_id"], line.get("policy_scope", quote.policy_scope), line.get("policy_id"))
        requested_quantity = quantity if quantity is not None else int(line["quantity"])
        proposal = ActionProposal(customer_id=customer.id, product_id=product.id, quantity=requested_quantity,
                                  requested_discount_pct=requested_discount_pct)
        decision, breakdown, checks = evaluate_quote_with_trace(actor, customer, product, policy, proposal, line.get("policy_scope", quote.policy_scope))
        review_required = review_required or decision.decision == "REVIEW"
        all_allowed = all_allowed and decision.decision == "ALLOW"
        all_review_eligible = all_review_eligible and decision.decision in {"ALLOW", "REVIEW"}
        requested_quote_lines.append({"product_id": product.id, "quantity": requested_quantity,
                                      "discount_pct": str(requested_discount_pct), "policy_id": policy.policy_id,
                                      "policy_version": policy.version, "policy_source_hash": policy_source_hash(policy)})
        requested_evaluations.append({"decision": decision.model_dump(mode="json")})
        requested_breakdown = breakdown or calculate_breakdown(product, requested_quantity, requested_discount_pct)
        request_items.append({"product_id": product.id, "quantity": requested_quantity,
                              "requested_discount_pct": str(requested_discount_pct),
                              "currency": product.currency, "total": str(requested_breakdown.total),
                              "decision": decision.model_dump(mode="json"), "guard_checks": checks})
        alt_quantity = max(1, min(requested_quantity, product.stock))
        alt_discount = min(requested_discount_pct, policy.max_discount_pct or Decimal("0"))
        alt_proposal = ActionProposal(customer_id=customer.id, product_id=product.id, quantity=alt_quantity,
                                     requested_discount_pct=alt_discount)
        alt_decision, alt_breakdown, alt_checks = evaluate_quote_with_trace(actor, customer, product, policy, alt_proposal, line.get("policy_scope", quote.policy_scope))
        alternative_items.append({"product_id": product.id, "quantity": alt_quantity,
                                  "discount_pct": str(alt_discount),
                                  "decision": alt_decision.model_dump(mode="json"),
                                  "total": str(alt_breakdown.total) if alt_breakdown else None,
                                  "currency": product.currency, "policy_id": policy.policy_id,
                                  "policy_version": policy.version, "guard_checks": alt_checks})
        alternative_products.append(product)
        requested_products.append(product)
    requested_failure = order_constraint_failure([
        (product.id, product.currency, item["quantity"], product.stock)
        for item, product in zip(request_items, requested_products)
    ])
    aggregate_failure = order_constraint_failure([
        (product.id, product.currency, item["quantity"], product.stock)
        for item, product in zip(alternative_items, alternative_products)
    ])
    direct_validated = (bool(alternative_items)
                        and all(item["decision"]["decision"] == "ALLOW" for item in alternative_items)
                        and aggregate_failure is None)
    if aggregate_failure:
        code, product_id, requested, available = aggregate_failure
        alternative_reason = {"code": code, "product_id": product_id, "requested": requested, "available": available}
    else:
        alternative_reason = None
    requested_review_ready = review_required and all_review_eligible and requested_failure is None
    requested_allowed = all_allowed and requested_failure is None
    approval_already_granted = (review_required and requested_failure is None
                                and _matches_approved_review(db, quote, requested_quote_lines, requested_evaluations))
    return {
        "quote_id": quote.id,
        "mode": "deterministic_guarded_preview",
        "requested_decision": "ALLOW" if requested_allowed else "REVIEW" if requested_review_ready else "DENY",
        "requested_items": request_items,
        "manager_review_available": requested_review_ready,
        "approval_already_granted": approval_already_granted,
        "alternative": {"decision": "ALLOW" if direct_validated else "DENY", "items": alternative_items,
                        "explanation": "Direct-limit alternatives were rechecked by Guard. No quote was changed or created.",
                        "reason": alternative_reason},
        "writes_performed": False,
    }


def request_counteroffer_approval(db: Session, actor: DemoActor, quote: QuoteDraft, customer: Customer,
                                  requested_discount_pct: Decimal, quantity: int | None = None) -> dict:
    if quote.status not in {"DRAFT", "REVISED", "ISSUED", "REJECTED", "EXPIRED"}:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_STATE_CONFLICT", "message": "This quote cannot receive counteroffers in its current state."})
    lines = _stored_lines(quote)
    if quantity is not None and len(lines) != 1:
        raise HTTPException(status_code=422, detail={"code": "COUNTEROFFER_QUANTITY_MULTI_ITEM", "message": "Set a revised quantity per product by creating a new inquiry. This approval request accepts one quantity for single-item quotes."})
    requested_lines = []
    saved_lines = []
    policy_context = []
    review_found = False
    now = _now()
    for line in lines:
        scope = line.get("policy_scope", quote.policy_scope)
        product, policy = _catalog_context(db, actor, customer, line["product_id"], scope, line.get("policy_id"))
        requested_quantity = quantity if quantity is not None else int(line["quantity"])
        proposal = ActionProposal(customer_id=customer.id, product_id=product.id, quantity=requested_quantity,
                                  requested_discount_pct=requested_discount_pct)
        decision, _, checks = evaluate_quote_with_trace(actor, customer, product, policy, proposal, scope)
        if decision.decision not in {"ALLOW", "REVIEW"}:
            raise HTTPException(status_code=409, detail={"code": decision.reason_code, "message": decision.human_message, "product_id": product.id})
        review_found = review_found or decision.decision == "REVIEW"
        requested_lines.append((product.id, product.currency, requested_quantity, product.stock))
        saved_lines.append({
            "proposal": proposal.model_dump(mode="json"), "policy_id": policy.policy_id,
            "policy_version": policy.version, "policy_source": policy.source,
            "policy_provenance": {"policy_source_hash": policy_source_hash(policy), "guard_checks": checks,
                                  "authorization_rule": {"decision": decision.decision, "reason_code": decision.reason_code}},
            "available_stock": product.stock, "inventory_checked_at": now.isoformat(),
        })
        policy_context.append((product, policy, requested_quantity))
    failure = order_constraint_failure(requested_lines)
    if failure:
        code, product_id, requested, available = failure
        raise HTTPException(status_code=409, detail={"code": code, "message": f"Approval request cannot be submitted: {product_id or 'currency'}; requested {requested}, available {available}."})
    if not review_found:
        raise HTTPException(status_code=409, detail={"code": "MANAGER_REVIEW_NOT_REQUIRED", "message": "The current policy does not require manager review for this counteroffer."})

    pending = db.scalar(select(ApprovalRequest).where(
        ApprovalRequest.tenant_id == actor.tenant_id,
        ApprovalRequest.quote_id == quote.id,
        ApprovalRequest.state == "PENDING",
    ))
    if pending:
        return {"approval_id": pending.id, "state": pending.state, "idempotent_replay": True}

    records = db.scalars(select(RunRecord).where(
        RunRecord.tenant_id == actor.tenant_id,
        RunRecord.actor_id == actor.id,
    ).order_by(RunRecord.created_at.desc()).limit(100)).all()
    source_run = next((record for record in records if (record.result or {}).get("quote", {}).get("id") == quote.id), None)
    if source_run is None:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_SOURCE_RUN_MISSING", "message": "The inquiry run for this quote is unavailable; no approval request was saved."})

    first_product, first_policy, _ = policy_context[0]
    approval = ApprovalRequest(
        run_id=source_run.id, applicant_actor_id=actor.id, customer_id=customer.id,
        product_key=first_product.internal_id, product_id=first_product.sku,
        quantity=sum(item[2] for item in policy_context), discount_pct=requested_discount_pct,
        policy_id=first_policy.policy_id, policy_version=first_policy.version,
        policy_scope=first_policy.policy_scope, lines=saved_lines, state="PENDING",
        reason="COUNTEROFFER_REVIEW", quote_id=quote.id, tenant_id=actor.tenant_id,
    )
    db.add(approval)
    db.flush()
    record_result = dict(source_run.result or {})
    trace = list(record_result.get("trace", []))
    trace.append({"step": "counteroffer_approval", "status": "pending",
                  "message": "A counteroffer was submitted for manager review; no new quote was created.",
                  "timestamp": now.isoformat(), "details": {"approval_id": approval.id, "reason_code": "COUNTEROFFER_REVIEW"}})
    record_result["trace"] = trace
    record_result["approval_request"] = {"id": approval.id, "state": "PENDING", "reason": "COUNTEROFFER_REVIEW"}
    source_run.result = record_result
    db.commit()
    return {"approval_id": approval.id, "state": approval.state, "idempotent_replay": False}


def _snapshot(db: Session, actor: DemoActor, quote: QuoteDraft, customer: Customer, version: int, expires_at: datetime) -> dict:
    lines = [{key: value for key, value in line.items() if key != "product_key"} for line in _stored_lines(quote)]
    approvals = db.scalars(select(ApprovalRequest).where(
        ApprovalRequest.quote_id == quote.id, ApprovalRequest.tenant_id == quote.tenant_id,
        ApprovalRequest.state == "APPROVED",
    )).all()
    return {
        "quote_id": quote.id, "version": version, "status": "ISSUED",
        "issued_by": {"actor_id": actor.id, "role": actor.role},
        "approvals": [{"approval_id": item.id, "approved_by": item.resolved_by,
                       "approved_at": item.resolved_at.isoformat() if item.resolved_at else None,
                       "policy_id": item.policy_id, "policy_version": item.policy_version,
                       "discount_pct": str(item.discount_pct)} for item in approvals],
        "customer_id": quote.customer_id, "customer_name": customer.name,
        "currency": lines[0].get("currency", "USD"), "policy_id": quote.policy_id,
        "policy_version": quote.policy_version, "subtotal": str(quote.subtotal),
        "discount_amount": str(quote.discount_amount), "total": str(quote.total),
        "lines": lines, "issued_at": _now().isoformat(), "expires_at": expires_at.isoformat(),
    }


def issue_quote(db: Session, actor: DemoActor, quote: QuoteDraft) -> dict:
    if quote.status not in {"DRAFT", "REVISED"}:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_STATE_CONFLICT", "message": "Only a draft or revised quote can be issued."})
    customer = db.scalar(select(Customer).where(Customer.id == quote.customer_id, Customer.tenant_id == actor.tenant_id))
    if customer is None:
        raise HTTPException(status_code=409, detail={"code": "CUSTOMER_UNAVAILABLE", "message": "The quote customer is unavailable."})
    requested = [QuoteChangeLine(product_id=line["product_id"], quantity=int(line["quantity"]),
                                 requested_discount_pct=Decimal(line["discount_pct"])) for line in _stored_lines(quote)]
    old_status = quote.status
    lines, evaluations = _evaluate_lines(db, actor, customer, requested, _stored_lines(quote), allow_review=True)
    if not _matches_approved_review(db, quote, lines, evaluations):
        review = next(item["decision"] for item in evaluations if item["decision"]["decision"] == "REVIEW")
        raise HTTPException(status_code=409, detail={"code": "MANAGER_APPROVAL_REQUIRED", "message": review["human_message"]})
    _set_quote_totals(quote, lines)
    version = (db.scalar(select(func.max(QuoteRevision.version)).where(
        QuoteRevision.quote_id == quote.id, QuoteRevision.tenant_id == actor.tenant_id,
    )) or 0) + 1
    expires_at = _now() + PUBLIC_QUOTE_TTL
    revision = QuoteRevision(tenant_id=actor.tenant_id, quote_id=quote.id, version=version,
                             status="ISSUED", snapshot={}, created_by=actor.id, expires_at=expires_at)
    quote.status = "ISSUED"
    db.add(revision)
    db.flush()
    revision.snapshot = _snapshot(db, actor, quote, customer, version, expires_at)
    raw_token = secrets.token_urlsafe(32)
    db.add(QuoteAccessToken(tenant_id=actor.tenant_id, revision_id=revision.id,
                            token_hash=sha256(raw_token.encode()).hexdigest(), expires_at=expires_at))
    _add_event(db, quote, old=old_status, new="ISSUED", actor=actor,
               reason="QUOTE_ISSUED", revision_id=revision.id, details={"version": version})
    db.commit()
    return {"quote_id": quote.id, "revision_id": revision.id, "version": version,
            "status": quote.status, "expires_at": expires_at.isoformat(), "access_token": raw_token,
            "guard_evaluations": evaluations}


def revise_quote(db: Session, actor: DemoActor, quote: QuoteDraft, request_lines: list[QuoteChangeLine]) -> dict:
    if quote.status not in {"DRAFT", "REVISED", "ISSUED", "REJECTED", "EXPIRED"}:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_STATE_CONFLICT", "message": "This quote cannot be revised in its current state."})
    customer = db.scalar(select(Customer).where(Customer.id == quote.customer_id, Customer.tenant_id == actor.tenant_id))
    if customer is None:
        raise HTTPException(status_code=409, detail={"code": "CUSTOMER_UNAVAILABLE", "message": "The quote customer is unavailable."})
    old_status = quote.status
    current = db.scalar(select(QuoteRevision).where(QuoteRevision.quote_id == quote.id,
                                                    QuoteRevision.tenant_id == actor.tenant_id).order_by(QuoteRevision.version.desc()))
    if old_status not in {"DRAFT", "REVISED"} and current is None:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_REVISION_MISSING", "message": "The issued quote snapshot is unavailable."})
    new_lines, evaluations = _evaluate_lines(db, actor, customer, request_lines, _stored_lines(quote), allow_review=True)
    if not _matches_approved_review(db, quote, new_lines, evaluations):
        review = next((item["decision"] for item in evaluations if item["decision"]["decision"] == "REVIEW"), None)
        if review:
            raise HTTPException(status_code=409, detail={"code": "MANAGER_APPROVAL_REQUIRED", "message": review["human_message"]})
        raise HTTPException(status_code=409, detail={"code": "QUOTE_REVISION_NOT_APPROVED", "message": "The quote revision did not pass Guard checks."})
    if current is not None and old_status not in {"DRAFT", "REVISED"}:
        current.status = "SUPERSEDED"
    _set_quote_totals(quote, new_lines)
    quote.status = "REVISED"
    if current is not None and old_status not in {"DRAFT", "REVISED"}:
        for access in db.scalars(select(QuoteAccessToken).where(QuoteAccessToken.revision_id == current.id)).all():
            access.revoked_at = _now()
    _add_event(db, quote, old=old_status, new="REVISED", actor=actor, reason="QUOTE_REVISION_CREATED",
               revision_id=current.id if current else None, details={"previous_version": current.version if current else None, "guard_evaluations": evaluations})
    db.commit()
    return {"quote_id": quote.id, "status": quote.status, "previous_version": current.version if current else None,
            "message": "Revised values passed Guard and are saved as an unpublished revision. Issue it to create the next customer-visible version."}


def quote_history(db: Session, quote: QuoteDraft, tenant_id: str) -> dict:
    revisions = db.scalars(select(QuoteRevision).where(QuoteRevision.quote_id == quote.id,
                                                       QuoteRevision.tenant_id == tenant_id).order_by(QuoteRevision.version.desc())).all()
    events = db.scalars(select(QuoteStatusEvent).where(QuoteStatusEvent.quote_id == quote.id,
                                                       QuoteStatusEvent.tenant_id == tenant_id).order_by(QuoteStatusEvent.created_at.desc())).all()
    return {"quote_id": quote.id, "status": quote.status,
            "revisions": [{"id": row.id, "version": row.version, "status": row.status,
                           "issued_at": row.issued_at, "expires_at": row.expires_at, "snapshot": row.snapshot} for row in revisions],
            "events": [{"id": row.id, "from_status": row.from_status, "to_status": row.to_status,
                        "actor_type": row.actor_type, "reason_code": row.reason_code,
                        "idempotency_key": row.idempotency_key, "created_at": row.created_at, "details": row.details} for row in events]}


def _current_public_access(db: Session, token: str) -> tuple[QuoteAccessToken, QuoteRevision, QuoteDraft]:
    token_hash = sha256(token.encode()).hexdigest()
    access = db.scalar(select(QuoteAccessToken).where(QuoteAccessToken.token_hash == token_hash))
    if access is None or access.revoked_at is not None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_LINK_INVALID", "message": "This quote link is invalid."})
    revision = db.get(QuoteRevision, access.revision_id)
    quote = db.scalar(select(QuoteDraft).where(QuoteDraft.id == revision.quote_id,
                                               QuoteDraft.tenant_id == access.tenant_id)) if revision else None
    if revision is None or quote is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_LINK_INVALID", "message": "This quote link is invalid."})
    latest = db.scalar(select(func.max(QuoteRevision.version)).where(QuoteRevision.quote_id == quote.id,
                                                                    QuoteRevision.tenant_id == quote.tenant_id))
    if revision.version != latest:
        raise HTTPException(status_code=410, detail={"code": "QUOTE_LINK_SUPERSEDED", "message": "This quote version has been superseded."})
    if access.expires_at.replace(tzinfo=timezone.utc) <= _now() or revision.expires_at.replace(tzinfo=timezone.utc) <= _now():
        if quote.status == "ISSUED":
            quote.status = "EXPIRED"; revision.status = "EXPIRED"
            _add_event(db, quote, old="ISSUED", new="EXPIRED", actor=None, reason="QUOTE_EXPIRED", revision_id=revision.id)
            db.commit()
        raise HTTPException(status_code=410, detail={"code": "QUOTE_EXPIRED", "message": "This quotation has expired."})
    return access, revision, quote


def public_quote(db: Session, token: str) -> dict:
    _, revision, quote = _current_public_access(db, token)
    if quote.status not in {"ISSUED", "ACCEPTED", "REJECTED"}:
        raise HTTPException(status_code=410, detail={"code": "QUOTE_NOT_ACTIONABLE", "message": "This quotation is no longer open for a response."})
    snapshot = revision.snapshot
    public_lines = [{key: line[key] for key in (
        "product_id", "product_name", "currency", "quantity", "unit_price", "discount_pct",
        "subtotal", "discount_amount", "total",
    ) if key in line} for line in snapshot["lines"]]
    return {key: snapshot[key] for key in (
        "quote_id", "version", "status", "customer_name", "currency", "subtotal",
        "discount_amount", "total", "issued_at", "expires_at",
    )} | {"status": quote.status, "lines": public_lines}


def respond_to_public_quote(db: Session, token: str, decision: str, reason: str | None, idempotency_key: str) -> dict:
    access, revision, quote = _current_public_access(db, token)
    prior = db.scalar(select(QuoteStatusEvent).where(
        QuoteStatusEvent.tenant_id == access.tenant_id,
        QuoteStatusEvent.quote_id == quote.id,
        QuoteStatusEvent.idempotency_key == idempotency_key,
    ))
    if prior:
        if prior.to_status != ("ACCEPTED" if decision == "ACCEPT" else "REJECTED"):
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "This idempotency key was already used for a different response."})
        return {"quote_id": quote.id, "revision": revision.version, "status": prior.to_status, "idempotent_replay": True}
    if quote.status != "ISSUED":
        raise HTTPException(status_code=409, detail={"code": "QUOTE_NOT_ACTIONABLE", "message": "This quotation has already been answered."})
    to_status = "ACCEPTED" if decision == "ACCEPT" else "REJECTED"
    quote.status = to_status
    revision.status = to_status
    event = _add_event(db, quote, old="ISSUED", new=to_status, actor=None,
                       reason="CUSTOMER_ACCEPTED" if to_status == "ACCEPTED" else "CUSTOMER_REJECTED",
                       revision_id=revision.id, key=idempotency_key, details={"reason": reason} if reason else {})
    db.commit()
    return {"quote_id": quote.id, "revision": revision.version, "status": event.to_status, "idempotent_replay": False}
