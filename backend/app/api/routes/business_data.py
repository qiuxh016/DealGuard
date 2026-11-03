from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Customer, DemoActor, Product
from app.services.actors import get_current_actor
from app.services.permissions import require_capability

router = APIRouter(tags=["business data"])


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    tier: str = Field(pattern="^(VIP|Standard)$")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Customer name cannot be blank.")
        return value


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=2, max_length=80, pattern="^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=160)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    stock: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Product name cannot be blank.")
        return value


@router.get("/customers")
def list_customers(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "business_data.read", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view customers.")
    query = select(Customer).where(Customer.tenant_id == actor.tenant_id)
    if actor.role == "sales":
        query = query.where(Customer.id == actor.customer_id)
    rows = db.scalars(query.order_by(Customer.name)).all()
    return [{"id": row.id, "name": row.name, "tier": row.tier} for row in rows]


@router.post("/customers", status_code=201)
def create_customer(body: CustomerCreate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can add customers.")
    row = Customer(id=f"C-{uuid4().hex[:12].upper()}", name=body.name.strip(), tier=body.tier, tenant_id=actor.tenant_id)
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "tier": row.tier}


@router.get("/products")
def list_products(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "business_data.read", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view products.")
    rows = db.scalars(select(Product).where(Product.tenant_id == actor.tenant_id).order_by(Product.id)).all()
    return [{"id": row.id, "name": row.name, "currency": row.currency, "unit_price": row.unit_price, "stock": row.stock} for row in rows]


@router.post("/products", status_code=201)
def create_product(body: ProductCreate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can add products.")
    if db.scalar(select(Product.id).where(Product.id == body.id.upper(), Product.tenant_id == actor.tenant_id)):
        raise HTTPException(status_code=409, detail={"code": "PRODUCT_ALREADY_EXISTS", "message": "This product ID already exists in the workspace."})
    row = Product(id=body.id.upper(), name=body.name.strip(), currency=body.currency, unit_price=body.unit_price, stock=body.stock, tenant_id=actor.tenant_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PRODUCT_ALREADY_EXISTS", "message": "This product ID already exists."}) from error
    return {"id": row.id, "name": row.name, "currency": row.currency, "unit_price": row.unit_price, "stock": row.stock}
