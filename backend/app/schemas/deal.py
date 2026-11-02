from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class InquiryRequest(BaseModel):
    inquiry: str = Field(min_length=1, max_length=10_000)


class InquiryIntent(BaseModel):
    product_id: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    requested_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    customer_message: str


class ActionProposal(BaseModel):
    kind: Literal["create_quote_draft", "update_quote_draft"] = "create_quote_draft"
    customer_id: str
    product_id: str
    quantity: int = Field(gt=0)
    requested_discount_pct: Decimal = Field(ge=0, le=100)
    target_quote_id: str | None = None


class PolicyEvidence(BaseModel):
    policy_id: str
    version: str
    title: str
    citation: str
    source: str
    retrieval_mode: Literal["chroma", "demo"]


class GuardDecision(BaseModel):
    decision: Literal["ALLOW", "REVIEW", "DENY", "NEEDS_INPUT"]
    reason_code: str
    policy_id: str | None = None
    policy_version: str | None = None
    human_message: str


class QuoteBreakdown(BaseModel):
    currency: str = "USD"
    unit_price: Decimal
    quantity: int
    discount_pct: Decimal
    subtotal: Decimal
    discount_amount: Decimal
    total: Decimal


class QuoteDraftResponse(BaseModel):
    id: str
    status: Literal["DRAFT"]
    customer_id: str
    product_id: str
    breakdown: QuoteBreakdown
    policy_id: str
    policy_version: str
    created_at: datetime


class TraceEvent(BaseModel):
    step: str
    status: str
    message: str
    timestamp: datetime
    details: dict = Field(default_factory=dict)


class RunResponse(BaseModel):
    run_id: str
    status: Literal["completed", "needs_input", "failed"]
    execution_mode: Literal["llm", "demo"]
    intent: InquiryIntent | None = None
    decision: GuardDecision | None = None
    quote: QuoteDraftResponse | None = None
    policy_evidence: list[PolicyEvidence] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    approval_request: "ApprovalRequestResponse | None" = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class PolicyResponse(BaseModel):
    id: str
    policy_id: str
    version: str
    title: str
    status: Literal["active", "inactive"]
    max_discount_pct: Decimal
    review_max_discount_pct: Decimal | None = None
    exception_approval_allowed: bool
    source: str


class QuoteUpdateRequest(BaseModel):
    requested_discount_pct: Decimal = Field(ge=0, le=100)


class ApprovalRequestResponse(BaseModel):
    id: str
    run_id: str
    applicant_actor_id: str
    customer_id: str
    product_id: str
    quantity: int
    discount_pct: Decimal
    policy_id: str
    policy_version: str
    state: Literal["PENDING", "APPROVED", "REJECTED", "STALE"]
    reason: str | None = None
    created_at: datetime


class ApprovalOutcome(BaseModel):
    approval: ApprovalRequestResponse
    quote: QuoteDraftResponse | None = None
    trace: list[TraceEvent] = Field(default_factory=list)


class ApprovalRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def require_reason_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Rejection reason cannot be blank.")
        return value


class PolicyDraftUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    customer_tier: Literal["VIP", "Standard"] = "VIP"
    max_discount_pct: Decimal = Field(ge=0, le=100)
    review_max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    exception_approval_allowed: bool = False


class PolicyDraftResponse(BaseModel):
    id: str
    policy_id: str
    version: str
    title: str
    customer_tier: str
    max_discount_pct: Decimal | None
    review_max_discount_pct: Decimal | None
    exception_approval_allowed: bool
    status: str
    source: str


class PolicyDiffRequest(BaseModel):
    inquiry: str = Field(min_length=1, max_length=10_000)
    base_policy_id: str
    base_version: str
    compare_policy_id: str
    compare_version: str


class PolicyDiffSide(BaseModel):
    policy_id: str
    version: str
    title: str
    citation: str
    decision: GuardDecision
    breakdown: QuoteBreakdown | None = None


class PolicyDiffResponse(BaseModel):
    intent: InquiryIntent | None
    base: PolicyDiffSide
    compare: PolicyDiffSide


class MetricsResponse(BaseModel):
    run_count: int
    allow_count: int
    review_count: int
    deny_count: int
    needs_input_count: int
    failed_count: int
    avg_duration_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
