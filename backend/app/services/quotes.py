from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Customer, DemoActor, Product, QuoteDraft
from app.guards.business_rules import evaluate_quote_with_trace
from app.rag.provenance import policy_source_hash, quote_provenance
from app.schemas.deal import ActionProposal, QuoteBreakdown, QuoteDraftResponse, QuoteUpdateRequest
from app.services.permissions import has_capability
from app.services.policies import current_quote_policies


def quote_response(row: QuoteDraft) -> QuoteDraftResponse:
    return QuoteDraftResponse(
        id=row.id, status="DRAFT", customer_id=row.customer_id, product_id=row.product_id,
        breakdown=QuoteBreakdown(
            currency=(row.lines or [{}])[0].get("currency", "USD"), unit_price=row.unit_price, quantity=row.quantity,
            discount_pct=row.discount_pct, subtotal=row.subtotal,
            discount_amount=row.discount_amount, total=row.total,
        ),
        policy_id=row.policy_id, policy_version=row.policy_version, policy_scope=row.policy_scope, created_at=row.created_at,
        lines=row.lines or [],
    )


def update_quote(db: Session, actor: DemoActor, quote_id: str, body: QuoteUpdateRequest) -> QuoteDraftResponse:
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    if not has_capability(actor, "quote.update_own") or row.owner_actor_id != actor.id or row.customer_id != actor.customer_id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "This account cannot update this quote."})
    if row.lines and len(row.lines) > 1:
        raise HTTPException(status_code=409, detail={"code": "MULTI_ITEM_UPDATE_UNSUPPORTED", "message": "Multi-item quote lines are immutable in this demo; create a new quote to change requested discounts."})
    product = db.query(Product).filter(Product.id == row.product_id, Product.tenant_id == actor.tenant_id).first()
    customer = db.query(Customer).filter(Customer.id == row.customer_id, Customer.tenant_id == actor.tenant_id).first()
    active_policies = current_quote_policies(db, actor.tenant_id, customer.tier, row.policy_id) if customer else []
    if product is None or customer is None or len(active_policies) != 1:
        raise HTTPException(status_code=409, detail={"code": "CURRENT_CONTEXT_UNAVAILABLE", "message": "Current quote context is unavailable; the draft was not changed."})
    active_policy = active_policies[0]
    proposal = ActionProposal(
        kind="update_quote_draft", customer_id=row.customer_id, product_id=row.product_id,
        quantity=row.quantity, requested_discount_pct=body.requested_discount_pct,
        target_quote_id=row.id,
    )
    decision, breakdown, guard_checks = evaluate_quote_with_trace(actor, customer, product, active_policy, proposal, row.policy_scope)
    if decision.decision != "ALLOW" or breakdown is None:
        raise HTTPException(status_code=409, detail={"code": decision.reason_code, "message": decision.human_message})
    row.unit_price = breakdown.unit_price
    row.discount_pct = breakdown.discount_pct
    row.subtotal = breakdown.subtotal
    row.discount_amount = breakdown.discount_amount
    row.total = breakdown.total
    row.policy_version = active_policy.version
    old_line = (row.lines or [{}])[0]
    same_source = (
        old_line.get("policy_source_hash") == policy_source_hash(active_policy)
        and old_line.get("policy_id") == active_policy.policy_id
        and old_line.get("policy_version") == active_policy.version
    )
    line = {
        **old_line,
        "product_id": product.id,
        "product_name": product.name,
        "currency": product.currency,
        "quantity": proposal.quantity,
        "unit_price": str(breakdown.unit_price),
        "discount_pct": str(breakdown.discount_pct),
        "subtotal": str(breakdown.subtotal),
        "discount_amount": str(breakdown.discount_amount),
        "total": str(breakdown.total),
        "policy_id": active_policy.policy_id,
        "policy_version": active_policy.version,
        "policy_source": active_policy.source,
        "policy_scope": row.policy_scope,
        "available_stock": product.stock,
    }
    if not same_source:
        line["citation"] = None
        line["citation_ref"] = None
        line["chunk_id"] = None
    line.update(quote_provenance(
        active_policy, decision, guard_checks,
        line.get("citation"), line.get("citation_ref"), line.get("chunk_id"),
    ))
    row.lines = [line]
    db.commit()
    db.refresh(row)
    return quote_response(row)
