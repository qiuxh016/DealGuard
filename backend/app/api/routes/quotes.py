import csv
import json
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from sqlalchemy import func, or_, select

from app.db.models import Customer, DemoActor, QuoteDraft
from app.schemas.deal import QuoteDraftResponse, QuoteUpdateRequest
from app.services.actors import get_current_actor
from app.services.permissions import has_capability
from app.db.models import DemoActor
from app.services.quotes import quote_response, update_quote

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("")
def list_quotes(
    search: str = Query(default="", max_length=120),
    status: str | None = Query(default=None, pattern="^DRAFT$"),
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
    if status and status != "DRAFT":
        query = query.where(QuoteDraft.status == status)
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
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None or (not has_capability(actor, "quote.read_tenant") and (not has_capability(actor, "quote.read_own") or row.owner_actor_id != actor.id or row.customer_id != actor.customer_id)):
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
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


@router.get("/{quote_id}", response_model=QuoteDraftResponse)
def get_quote(
    quote_id: str,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> QuoteDraftResponse:
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    if not has_capability(actor, "quote.read_tenant") and (not has_capability(actor, "quote.read_own") or row.owner_actor_id != actor.id or row.customer_id != actor.customer_id):
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    return quote_response(row)


@router.patch("/{quote_id}", response_model=QuoteDraftResponse)
def patch_quote(
    quote_id: str,
    body: QuoteUpdateRequest,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> QuoteDraftResponse:
    return update_quote(db, actor, quote_id, body)
