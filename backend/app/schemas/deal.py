from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PolicyActionType = Literal["quote_discount", "reference_only"]
PolicyScope = Literal["general", "bulk_order", "loyalty", "early_payment", "seasonal_campaign", "product_specific"]


class InquiryRequest(BaseModel):
    inquiry: str = Field(min_length=1, max_length=10_000)


class InquiryIntent(BaseModel):
    items: list["QuoteItemIntent"] = Field(default_factory=list, max_length=20)
    product_id: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    requested_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    policy_scope: PolicyScope | None = None
    customer_message: str


class QuoteItemIntent(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    requested_discount_pct: Decimal = Field(ge=0, le=100)


class QuoteLineResponse(BaseModel):
    product_id: str
    product_name: str
    currency: str
    quantity: int
    unit_price: Decimal
    discount_pct: Decimal
    subtotal: Decimal
    discount_amount: Decimal
    total: Decimal
    policy_id: str
    policy_version: str
    policy_source: str
    policy_scope: PolicyScope = "general"
    available_stock: int | None = None
    inventory_checked_at: datetime | None = None


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
    retrieval_mode: Literal["chroma", "chroma_local", "chroma_local_consensus", "local_consensus", "demo", "registry"]


class GuardDecision(BaseModel):
    decision: Literal["ALLOW", "REVIEW", "DENY", "NEEDS_INPUT"]
    reason_code: str
    policy_id: str | None = None
    policy_version: str | None = None
    human_message: str
    suggested_discount_pct: Decimal | None = None
    suggested_product_id: str | None = None


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
    policy_scope: PolicyScope = "general"
    created_at: datetime
    lines: list[QuoteLineResponse] = Field(default_factory=list)


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
    action_type: PolicyActionType
    policy_scope: PolicyScope
    scope_product_id: str | None = None
    status: Literal["active", "inactive"]
    max_discount_pct: Decimal | None = None
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
    policy_scope: PolicyScope = "general"
    state: Literal["PENDING", "APPROVED", "REJECTED", "STALE"]
    reason: str | None = None
    created_at: datetime
    lines: list[dict] | None = None


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
    action_type: PolicyActionType
    policy_scope: PolicyScope = "general"
    scope_product_id: str | None = Field(default=None, min_length=1, max_length=80)
    customer_tier: Literal["VIP", "Standard"] = "VIP"
    max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    review_max_discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    exception_approval_allowed: bool = False

    @model_validator(mode="after")
    def validate_action_rules(self):
        if self.action_type == "reference_only" and (self.policy_scope != "general" or self.scope_product_id is not None):
            raise ValueError("Reference-only policies cannot define executable applicability scopes.")
        if self.policy_scope == "product_specific" and not self.scope_product_id:
            raise ValueError("Product-specific policies must identify the product they apply to.")
        if self.policy_scope != "product_specific" and self.scope_product_id is not None:
            raise ValueError("Only product-specific policies can identify a product scope.")
        if self.action_type == "quote_discount":
            if self.max_discount_pct is None:
                raise ValueError("A discount policy must define its direct discount limit.")
            if self.exception_approval_allowed and (self.review_max_discount_pct is None or self.review_max_discount_pct <= self.max_discount_pct):
                raise ValueError("The review limit must exceed the direct discount limit.")
            if not self.exception_approval_allowed and self.review_max_discount_pct is not None:
                raise ValueError("A review limit is only valid when manager exceptions are enabled.")
        elif self.max_discount_pct is not None or self.review_max_discount_pct is not None or self.exception_approval_allowed:
            raise ValueError("Reference-only policies cannot define quote discount rules.")
        return self


class PolicyDraftResponse(BaseModel):
    id: str
    policy_id: str
    version: str
    title: str
    action_type: PolicyActionType | None = None
    policy_scope: PolicyScope = "general"
    scope_product_id: str | None = None
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
    p50_duration_ms: int | None
    p95_duration_ms: int | None
    demo_run_count: int
    llm_run_count: int
    input_tokens: int | None
    output_tokens: int | None
