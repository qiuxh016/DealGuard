import csv
import json
from io import StringIO

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from sqlalchemy import func, or_, select

from app.db.models import Customer, DemoActor, QuoteDraft
from app.schemas.deal import CustomerQuoteResponse, CounterofferRequest, QuoteDraftResponse, QuoteRevisionRequest, QuoteUpdateRequest
from app.services.actors import get_current_actor
from app.services.permissions import has_capability
from app.db.models import DemoActor
from app.services.quotes import quote_response, update_quote
from app.services.quote_documents import email_draft, quotation_pdf
from app.services.quote_lifecycle import issue_quote, plan_counteroffer, public_quote, quote_history, request_counteroffer_approval, respond_to_public_quote, revise_quote

router = APIRouter(prefix="/quotes", tags=["quotes"])


def _readable_quote(db: Session, actor: DemoActor, quote_id: str) -> tuple[QuoteDraft, Customer]:
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None or (not has_capability(actor, "quote.read_tenant") and (not has_capability(actor, "quote.read_own") or row.owner_actor_id != actor.id or row.customer_id != actor.customer_id)):
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    customer = db.query(Customer).filter(Customer.id == row.customer_id, Customer.tenant_id == actor.tenant_id).first()
    if customer is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    return row, customer


@router.get("")
def list_quotes(
    search: str = Query(default="", max_length=120),
    status: str | None = Query(default=None, pattern="^(DRAFT|FORMAL)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    query = select(QuoteDraft, Customer.name).join(Customer, Customer.id == QuoteDraft.customer_id).where(
        QuoteDraft.tenant_id == actor.tenant_id,
    )
    if not has_capability(actor, "quote.read_tenant"):
        if not has_capability(actor, "quote.read_own"):
            raise HTTPException(status_code=403, detail={"code": "QUOTE_READ_ROLE_REQUIRED", "message": "This role cannot view quotes."})
        query = query.where(QuoteDraft.owner_actor_id == actor.id, QuoteDraft.customer_id == actor.customer_id)
    if status == "DRAFT":
        query = query.where(QuoteDraft.status.in_(("DRAFT", "REVISED")))
    elif status == "FORMAL":
        query = query.where(QuoteDraft.status.notin_(("DRAFT", "REVISED")))
    if search.strip():
        pattern = f"%{search.strip().lower()}%"
        query = query.where(or_(func.lower(QuoteDraft.id).like(pattern), func.lower(Customer.name).like(pattern), func.lower(QuoteDraft.policy_id).like(pattern)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(QuoteDraft.created_at.desc(), QuoteDraft.id).offset(offset).limit(limit)).all()
    return {
        "items": [
            {
                "id": quote.id,
                "status": quote.status,
                "customer_id": quote.customer_id,
                "customer_name": customer_name,
                "product_id": quote.product_id,
                "quantity": quote.quantity,
                "currency": (quote.lines or [{}])[0].get("currency", "USD"),
                "total": str(quote.total),
                "policy_id": quote.policy_id,
                "policy_version": quote.policy_version,
                "line_count": len(quote.lines) if quote.lines else 1,
                "created_at": quote.created_at,
            }
            for quote, customer_name in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{quote_id}/export.csv")
def export_quote_csv(quote_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row, _ = _readable_quote(db, actor, quote_id)
    output = StringIO(newline="")
    writer = csv.writer(output)
    def cell(value):
        return f"'{value}" if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value
    writer.writerow(["quote_id", "status", "customer_id", "product_id", "product_name", "quantity", "currency", "unit_price", "discount_pct", "subtotal", "discount_amount", "total", "policy_id", "policy_version", "policy_source", "citation", "citation_ref", "chunk_id", "policy_source_hash", "authorization_rule", "guard_evaluation"])
    if row.lines:
        for line in row.lines:
            writer.writerow([cell(row.id), cell(row.status), cell(row.customer_id), cell(line["product_id"]), cell(line["product_name"]), line["quantity"], line["currency"], line["unit_price"], line["discount_pct"], line["subtotal"], line["discount_amount"], line["total"], cell(line["policy_id"]), cell(line["policy_version"]), cell(line["policy_source"]), cell(line.get("citation") or ""), cell(line.get("citation_ref") or ""), cell(line.get("chunk_id") or ""), cell(line.get("policy_source_hash") or ""), cell(json.dumps(line.get("authorization_rule", {}), ensure_ascii=False, sort_keys=True)), cell(json.dumps(line.get("guard_evaluation", {}), ensure_ascii=False, sort_keys=True))])
    else:
        writer.writerow([cell(row.id), cell(row.status), cell(row.customer_id), cell(row.product_id), "", row.quantity, "USD", row.unit_price, row.discount_pct,
                         row.subtotal, row.discount_amount, row.total, cell(row.policy_id), cell(row.policy_version), "", "", "", "", "{}", "{}"])
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="dealguard-{row.id}.csv"'})


@router.get("/{quote_id}/email-draft")
def get_quote_email_draft(
    quote_id: str,
    locale: str = Query(default="en", pattern="^(en|zh)$"),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    row, customer = _readable_quote(db, actor, quote_id)
    return email_draft(row, customer, locale)


@router.post("/{quote_id}/counteroffer")
def preview_counteroffer(quote_id: str, body: CounterofferRequest,
                        actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row, customer = _readable_quote(db, actor, quote_id)
    if not has_capability(actor, "quote.update_own") or row.owner_actor_id != actor.id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "Only the quote owner can plan a counteroffer."})
    return plan_counteroffer(db, actor, row, customer, body.requested_discount_pct, body.quantity)


@router.post("/{quote_id}/counteroffer/approval")
def submit_counteroffer_approval(quote_id: str, body: CounterofferRequest,
                                 actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row, customer = _readable_quote(db, actor, quote_id)
    if not has_capability(actor, "quote.update_own") or row.owner_actor_id != actor.id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "Only the quote owner can submit a counteroffer for review."})
    return request_counteroffer_approval(db, actor, row, customer, body.requested_discount_pct, body.quantity)


@router.get("/{quote_id}/history")
def get_quote_history(quote_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row, _ = _readable_quote(db, actor, quote_id)
    return quote_history(db, row, actor.tenant_id)


@router.post("/{quote_id}/issue")
def issue_quote_to_customer(quote_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row, _ = _readable_quote(db, actor, quote_id)
    if not has_capability(actor, "quote.update_own") or row.owner_actor_id != actor.id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "Only the quote owner can issue this quote."})
    return issue_quote(db, actor, row)


@router.post("/{quote_id}/revise")
def revise_issued_quote(quote_id: str, body: QuoteRevisionRequest,
                        actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row, _ = _readable_quote(db, actor, quote_id)
    if not has_capability(actor, "quote.update_own") or row.owner_actor_id != actor.id:
        raise HTTPException(status_code=403, detail={"code": "RESOURCE_NOT_OWNED", "message": "Only the quote owner can revise this quote."})
    return revise_quote(db, actor, row, body.lines)


@router.get("/{quote_id}/quotation.pdf")
def export_quotation_pdf(
    quote_id: str,
    locale: str = Query(default="en", pattern="^(en|zh)$"),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    row, customer = _readable_quote(db, actor, quote_id)
    return Response(
        quotation_pdf(row, customer, locale),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="dealguard-quotation-{row.id}.pdf"'},
    )


@router.get("/public/{token}")
def get_public_quote(token: str, db: Session = Depends(get_db)):
    return public_quote(db, token)


@router.post("/public/{token}/response")
def respond_public_quote(token: str, body: CustomerQuoteResponse,
                         idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
                         db: Session = Depends(get_db)):
    return respond_to_public_quote(db, token, body.decision, body.reason, idempotency_key)


@router.get("/{quote_id}", response_model=QuoteDraftResponse)
def get_quote(
    quote_id: str,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> QuoteDraftResponse:
    row, _ = _readable_quote(db, actor, quote_id)
    return quote_response(row)


@router.patch("/{quote_id}", response_model=QuoteDraftResponse)
def patch_quote(
    quote_id: str,
    body: QuoteUpdateRequest,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> QuoteDraftResponse:
    return update_quote(db, actor, quote_id, body)
