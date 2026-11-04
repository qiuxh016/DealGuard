from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.db.database import get_db
from app.db.models import Customer, DemoActor, MediaAsset, Product
from app.services.actors import get_current_actor
from app.services.permissions import require_capability
from app.services.media_assets import attach_image, read_image, remove_image
from app.services.media_assets import create_image_asset
from app.services.catalog_import_images import image_preview, read_import_images
from app.services.customer_import import (
    MAX_CUSTOMER_IMPORT_BYTES,
    CustomerImportError,
    customer_import_template,
    parse_customer_import,
    public_customer_import_preview,
)
from app.services.product_import import (
    MAX_PRODUCT_IMPORT_BYTES,
    ProductImportError,
    parse_product_import,
    product_import_template,
    public_product_import_preview,
)

router = APIRouter(tags=["business data"])


def _product_import_context(file: UploadFile, content: bytes, actor: DemoActor, db: Session, images=None):
    try:
        rows = parse_product_import(content, file.filename or "")
    except ProductImportError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": "PRODUCT_IMPORT_FILE_INVALID", "message": str(error)}) from error
    sku_values = sorted({row["sku"].upper() for row in rows if row["sku"]})
    existing_skus: set[str] = set()
    for start in range(0, len(sku_values), 500):
        batch = sku_values[start:start + 500]
        existing_skus.update(
            sku.upper() for sku in db.scalars(
                select(Product.sku).where(Product.tenant_id == actor.tenant_id, func.upper(Product.sku).in_(batch))
            ).all()
        )
    preview = public_product_import_preview(rows, existing_skus)
    preview_rows, unmatched = image_preview(rows, images or {}, "sku")
    image_by_row = {row["row_number"]: row["image_filename"] for row in preview_rows}
    for row in preview["rows"]:
        row["image_filename"] = image_by_row.get(row["row_number"])
    preview["unmatched_images"] = unmatched
    return rows, preview


def _customer_import_context(file: UploadFile, content: bytes, actor: DemoActor, db: Session, images=None):
    try:
        rows = parse_customer_import(content, file.filename or "")
    except CustomerImportError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": "CUSTOMER_IMPORT_FILE_INVALID", "message": str(error)}) from error
    customer_ids = sorted({row["customer_id"].upper() for row in rows if row["customer_id"]})
    existing_ids = set()
    if customer_ids:
        existing_ids.update(
            value.upper() for value in db.scalars(
                select(Customer.id).where(Customer.tenant_id == actor.tenant_id, func.upper(Customer.id).in_(customer_ids))
            ).all()
        )
    preview = public_customer_import_preview(rows, existing_ids)
    preview_rows, unmatched = image_preview(rows, images or {}, "customer_id")
    image_by_row = {row["row_number"]: row["image_filename"] for row in preview_rows}
    for row in preview["rows"]:
        row["image_filename"] = image_by_row.get(row["row_number"])
    preview["unmatched_images"] = unmatched
    return rows, preview


async def _read_product_import_file(file: UploadFile) -> bytes:
    content = await file.read(MAX_PRODUCT_IMPORT_BYTES + 1)
    if len(content) > MAX_PRODUCT_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail={"code": "PRODUCT_IMPORT_TOO_LARGE", "message": "文件不能超过 5 MiB。"})
    return content


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

    id: str | None = Field(default=None, min_length=2, max_length=80, pattern="^[A-Za-z0-9_-]+$")
    sku: str | None = Field(default=None, min_length=2, max_length=80, pattern="^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=160)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    stock: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_sku(self):
        if not self.id and not self.sku:
            raise ValueError("A product SKU is required.")
        if self.id and self.sku and self.id.upper() != self.sku.upper():
            raise ValueError("id and sku must refer to the same product SKU when both are supplied.")
        return self

    @property
    def normalized_sku(self) -> str:
        return (self.sku or self.id or "").upper()

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Product name cannot be blank.")
        return value


@router.get("/customers")
def list_customers(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    if actor.role == "manager":
        require_capability(actor, "policy.diff", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot select a customer for policy comparison.")
    else:
        require_capability(actor, "business_data.read", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view customers.")
    query = select(Customer).where(Customer.tenant_id == actor.tenant_id)
    if actor.role == "sales":
        query = query.where(Customer.id == actor.customer_id)
    rows = db.scalars(query.order_by(Customer.name)).all()
    return [{"id": row.id, "name": row.name, "tier": row.tier, "image_id": row.image_asset_id} for row in rows]


@router.post("/customers", status_code=201)
def create_customer(body: CustomerCreate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can add customers.")
    row = Customer(id=f"C-{uuid4().hex[:12].upper()}", name=body.name.strip(), tier=body.tier, tenant_id=actor.tenant_id)
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "tier": row.tier, "image_id": row.image_asset_id}


@router.get("/customers/import/template")
def download_customer_import_template(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    actor: DemoActor = Depends(get_current_actor),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can import customers.")
    content, media_type, filename = customer_import_template(format)
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/customers/import/preview")
async def preview_customer_import(
    file: UploadFile = File(...),
    images: list[UploadFile] = File(default=[]),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can import customers.")
    content = await file.read(MAX_CUSTOMER_IMPORT_BYTES + 1)
    image_files = await read_import_images(images)
    _, preview = _customer_import_context(file, content, actor, db, image_files)
    return {"filename": file.filename, **preview}


@router.post("/customers/import")
async def import_customers(
    file: UploadFile = File(...),
    images: list[UploadFile] = File(default=[]),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can import customers.")
    content = await file.read(MAX_CUSTOMER_IMPORT_BYTES + 1)
    image_files = await read_import_images(images)
    rows, preview = _customer_import_context(file, content, actor, db, image_files)
    if preview["total_rows"] == 0:
        raise HTTPException(status_code=422, detail={"code": "CUSTOMER_IMPORT_EMPTY", "message": "文件中没有可导入的客户行。"})
    if preview["invalid_rows"]:
        raise HTTPException(status_code=422, detail={
            "code": "CUSTOMER_IMPORT_VALIDATION_FAILED",
            "message": "存在无效或重复客户行；没有导入任何客户。请修正文件后重新预览。",
            "preview": {"filename": file.filename, **preview},
        })
    customer_rows = [row for row in rows if row["customer"]]
    customers = [Customer(**row["customer"], tenant_id=actor.tenant_id) for row in customer_rows]
    db.add_all(customers)
    try:
        db.flush()
        for row, customer in zip(customer_rows, customers, strict=True):
            image = image_files.get(row["customer_id"].upper())
            if image:
                customer.image_asset_id = create_image_asset(db, actor, image[1], image[2])
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "CUSTOMER_IMPORT_CONFLICT",
            "message": "客户目录在预览后发生变化；没有导入任何客户，请重新预览。",
        }) from error
    return {"imported_count": len(customers), "unmatched_images": preview["unmatched_images"], "customers": [
        {"id": customer.id, "name": customer.name, "tier": customer.tier, "image_id": customer.image_asset_id}
        for customer in customers
    ]}


@router.post("/customers/{customer_id}/image")
async def upload_customer_image(
    customer_id: str,
    file: UploadFile = File(...),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can update customer images.")
    row = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "Customer not found in this workspace."})
    content, content_type = await read_image(file)
    return {"image_id": attach_image(db, actor, row, content, content_type)}


@router.delete("/customers/{customer_id}/image")
def delete_customer_image(
    customer_id: str,
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can update customer images.")
    row = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "Customer not found in this workspace."})
    remove_image(db, actor, row)
    return {"image_id": None}


@router.get("/products/import/template")
def download_product_import_template(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    actor: DemoActor = Depends(get_current_actor),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can import products.")
    content, media_type, filename = product_import_template(format)
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/products")
def list_products(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    if actor.role == "manager":
        require_capability(actor, "policy.diff", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view products for policy comparison.")
    else:
        require_capability(actor, "business_data.read", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view products.")
    rows = db.scalars(select(Product).where(Product.tenant_id == actor.tenant_id).order_by(Product.id)).all()
    return [{"id": row.sku, "sku": row.sku, "name": row.name, "currency": row.currency, "unit_price": row.unit_price, "stock": row.stock, "image_id": row.image_asset_id} for row in rows]


@router.post("/products/{sku}/image")
async def upload_product_image(
    sku: str,
    file: UploadFile = File(...),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can update product images.")
    row = db.scalar(select(Product).where(Product.sku == sku, Product.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "Product not found in this workspace."})
    content, content_type = await read_image(file)
    return {"image_id": attach_image(db, actor, row, content, content_type)}


@router.delete("/products/{sku}/image")
def delete_product_image(
    sku: str,
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can update product images.")
    row = db.scalar(select(Product).where(Product.sku == sku, Product.tenant_id == actor.tenant_id))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "Product not found in this workspace."})
    remove_image(db, actor, row)
    return {"image_id": None}


@router.get("/media/{asset_id}")
def get_media_asset(
    asset_id: str,
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    if actor.role == "manager":
        require_capability(actor, "policy.diff", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view workspace images.")
    else:
        require_capability(actor, "business_data.read", code="BUSINESS_DATA_ROLE_REQUIRED", message="This role cannot view workspace images.")
    asset = db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.tenant_id == actor.tenant_id))
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "IMAGE_NOT_FOUND", "message": "Image not found in this workspace."})
    return Response(content=asset.content, media_type=asset.content_type, headers={
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    })


@router.post("/products/import/preview")
async def preview_product_import(
    file: UploadFile = File(...),
    images: list[UploadFile] = File(default=[]),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can import products.")
    content = await _read_product_import_file(file)
    image_files = await read_import_images(images)
    _, preview = _product_import_context(file, content, actor, db, image_files)
    return {"filename": file.filename, **preview}


@router.post("/products/import")
async def import_products(
    file: UploadFile = File(...),
    images: list[UploadFile] = File(default=[]),
    actor: DemoActor = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can import products.")
    content = await _read_product_import_file(file)
    image_files = await read_import_images(images)
    rows, preview = _product_import_context(file, content, actor, db, image_files)
    if preview["total_rows"] == 0:
        raise HTTPException(status_code=422, detail={"code": "PRODUCT_IMPORT_EMPTY", "message": "文件中没有可导入的商品行。"})
    if preview["invalid_rows"]:
        raise HTTPException(status_code=422, detail={
            "code": "PRODUCT_IMPORT_VALIDATION_FAILED",
            "message": "存在无效或重复商品行；没有导入任何商品。请修正文件后重新预览。",
            "preview": {"filename": file.filename, **preview},
        })
    product_rows = [row for row in rows if row["product"]]
    products = [row["product"] for row in product_rows]
    product_models = [Product(**product, tenant_id=actor.tenant_id) for product in products]
    db.add_all(product_models)
    try:
        db.flush()
        for row, product_model in zip(product_rows, product_models, strict=True):
            image = image_files.get(row["sku"].upper())
            if image:
                product_model.image_asset_id = create_image_asset(db, actor, image[1], image[2])
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "PRODUCT_IMPORT_CONFLICT",
            "message": "商品目录在预览后发生变化；没有导入任何商品，请重新预览。",
        }) from error
    return {"imported_count": len(products), "unmatched_images": preview["unmatched_images"], "products": [
        {"id": product.sku, "sku": product.sku, "name": product.name, "currency": product.currency, "unit_price": product.unit_price, "stock": product.stock, "image_id": product.image_asset_id}
        for product in product_models
    ]}


@router.post("/products", status_code=201)
def create_product(body: ProductCreate, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "business_data.manage", code="BUSINESS_DATA_ADMIN_REQUIRED", message="Only workspace administrators can add products.")
    if db.scalar(select(Product.internal_id).where(Product.sku == body.normalized_sku, Product.tenant_id == actor.tenant_id)):
        raise HTTPException(status_code=409, detail={"code": "PRODUCT_ALREADY_EXISTS", "message": "This product SKU already exists in the workspace."})
    row = Product(sku=body.normalized_sku, name=body.name.strip(), currency=body.currency, unit_price=body.unit_price, stock=body.stock, tenant_id=actor.tenant_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "PRODUCT_ALREADY_EXISTS", "message": "This product ID already exists."}) from error
    return {"id": row.sku, "sku": row.sku, "name": row.name, "currency": row.currency, "unit_price": row.unit_price, "stock": row.stock, "image_id": row.image_asset_id}
