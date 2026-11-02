from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class AuthAccount(Base):
    __tablename__ = "auth_accounts"

    actor_id: Mapped[str] = mapped_column(ForeignKey("demo_actors.id"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    actor_id: Mapped[str] = mapped_column(ForeignKey("demo_actors.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="USD")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class DemoActor(Base):
    __tablename__ = "demo_actors"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    policy_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="inactive")
    customer_tier: Mapped[str] = mapped_column(String, nullable=False)
    max_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    review_max_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    exception_approval_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class QuoteDraft(Base):
    __tablename__ = "quote_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    owner_actor_id: Mapped[str] = mapped_column(ForeignKey("demo_actors.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    policy_id: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("tenant_id", "actor_id", "idempotency_key", name="uq_runs_tenant_actor_idempotency_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    inquiry: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    applicant_actor_id: Mapped[str] = mapped_column(ForeignKey("demo_actors.id"), nullable=False)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    policy_id: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("demo_actors.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quote_drafts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")


class PolicyDraft(Base):
    __tablename__ = "policy_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    policy_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default="Uploaded policy draft")
    customer_tier: Mapped[str] = mapped_column(String, nullable=False, default="VIP")
    max_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    review_max_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    exception_approval_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("demo_actors.id"), nullable=False)
    policy_row_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False, default="harborlight-demo")
