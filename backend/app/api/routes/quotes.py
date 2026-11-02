import csv
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import QuoteDraft
from app.schemas.deal import QuoteDraftResponse, QuoteUpdateRequest
from app.services.actors import get_current_actor
from app.db.models import DemoActor
from app.services.quotes import quote_response, update_quote

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("/{quote_id}/export.csv")
def export_quote_csv(quote_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None or (actor.role != "admin" and (row.owner_actor_id != actor.id or row.customer_id != actor.customer_id)):
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    output = StringIO(newline="")
    writer = csv.writer(output)
    def cell(value):
        return f"'{value}" if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value
    writer.writerow(["quote_id", "status", "customer_id", "product_id", "quantity", "currency", "unit_price", "discount_pct", "subtotal", "discount_amount", "total", "policy_id", "policy_version"])
    writer.writerow([cell(row.id), cell(row.status), cell(row.customer_id), cell(row.product_id), row.quantity, "USD", row.unit_price, row.discount_pct,
                     row.subtotal, row.discount_amount, row.total, cell(row.policy_id), cell(row.policy_version)])
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="dealguard-{row.id}.csv"'})


@router.get("/{quote_id}", response_model=QuoteDraftResponse)
def get_quote(
    quote_id: str,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> QuoteDraftResponse:
    row = db.query(QuoteDraft).filter(QuoteDraft.id == quote_id, QuoteDraft.tenant_id == actor.tenant_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    if actor.role != "admin" and (row.owner_actor_id != actor.id or row.customer_id != actor.customer_id):
        raise HTTPException(status_code=404, detail={"code": "QUOTE_NOT_FOUND", "message": "Quote draft not found."})
    return quote_response(row)


@router.patch("/{quote_id}", response_model=QuoteDraftResponse)
def patch_quote(
    quote_id: str,
    body: QuoteUpdateRequest,
    actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db),
) -> QuoteDraftResponse:
    return update_quote(db, actor, quote_id, body)
