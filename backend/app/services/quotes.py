from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Customer, DemoActor, Policy, Product, QuoteDraft
from app.guards.business_rules import evaluate_quote
from app.schemas.deal import ActionProposal, QuoteBreakdown, QuoteDraftResponse, QuoteUpdateRequest


def quote_response(row: QuoteDraft) -> QuoteDraftResponse:
    return QuoteDraftResponse(
        id=row.id, status="DRAFT", customer_id=row.customer_id, product_id=row.product_id,
        breakdown=QuoteBreakdown(
            currency="USD", unit_price=row.unit_price, quantity=row.quantity,
            discount_pct=row.discount_pct, subtotal=row.subtotal,
            discount_amount=row.discount_amount, total=row.total,
        ),
        policy_id=row.policy_id, policy_version=row.policy_version, created_at=row.created_at,
    )


def update_quote(db: Session, actor: DemoActor, quote_id: str, body: QuoteUpdateRequest) -> QuoteDraftResponse:
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    if actor.role != "sales" or row.owner_actor_id != actor.id or row.customer_id != actor.customer_id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "This demo actor cannot update this quote."})
    product = db.query(Product).filter(Product.id == row.product_id, Product.tenant_id == actor.tenant_id).first()
    active_policy = db.query(Policy).filter_by(tenant_id=actor.tenant_id, policy_id=row.policy_id, status="active", customer_tier="VIP").first()
    customer = db.query(Customer).filter(Customer.id == row.customer_id, Customer.tenant_id == actor.tenant_id).first()
    if product is None or customer is None or active_policy is None:
        raise HTTPException(status_code=409, detail={"code": "CURRENT_CONTEXT_UNAVAILABLE", "message": "Current quote context is unavailable; the draft was not changed."})
    proposal = ActionProposal(
        kind="update_quote_draft", customer_id=row.customer_id, product_id=row.product_id,
        quantity=row.quantity, requested_discount_pct=body.requested_discount_pct,
        target_quote_id=row.id,
    )
    decision, breakdown = evaluate_quote(actor, customer, product, active_policy, proposal)
    if decision.decision != "ALLOW" or breakdown is None:
        raise HTTPException(status_code=409, detail={"code": decision.reason_code, "message": decision.human_message})
    row.unit_price = breakdown.unit_price
    row.discount_pct = breakdown.discount_pct
    row.subtotal = breakdown.subtotal
    row.discount_amount = breakdown.discount_amount
    row.total = breakdown.total
    row.policy_version = active_policy.version
    db.commit()
    db.refresh(row)
    return quote_response(row)
