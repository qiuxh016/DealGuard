from decimal import Decimal, ROUND_HALF_UP

from app.db.models import Customer, DemoActor, Policy, Product
from app.schemas.deal import ActionProposal, GuardDecision, QuoteBreakdown

CENT = Decimal("0.01")


def calculate_breakdown(product: Product, quantity: int, discount_pct: Decimal) -> QuoteBreakdown:
    subtotal = (product.unit_price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
    discount_amount = (subtotal * discount_pct / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    total = (subtotal - discount_amount).quantize(CENT, rounding=ROUND_HALF_UP)
    return QuoteBreakdown(currency=product.currency, unit_price=product.unit_price, quantity=quantity,
                          discount_pct=discount_pct, subtotal=subtotal, discount_amount=discount_amount, total=total)


def evaluate_quote(
    actor: DemoActor,
    customer: Customer,
    product: Product,
    policy: Policy | None,
    proposal: ActionProposal,
) -> tuple[GuardDecision, QuoteBreakdown | None]:
    if actor.role != "sales":
        return GuardDecision(decision="DENY", reason_code="SALES_ROLE_REQUIRED", human_message="A sales demo actor is required to prepare a quote."), None
    if actor.customer_id != customer.id:
        return GuardDecision(decision="DENY", reason_code="RESOURCE_NOT_OWNED", human_message="This demo actor cannot quote for the selected customer."), None
    if customer.tier != "VIP" or policy is None:
        return GuardDecision(decision="DENY", reason_code="NO_APPLICABLE_POLICY", human_message="No active discount policy applies to this verified customer tier."), None
    if proposal.quantity <= 0:
        return GuardDecision(decision="DENY", reason_code="INVALID_QUANTITY", policy_id=policy.policy_id, policy_version=policy.version, human_message="Quantity must be greater than zero."), None
    if proposal.quantity > product.stock:
        return GuardDecision(decision="DENY", reason_code="INSUFFICIENT_STOCK", policy_id=policy.policy_id, policy_version=policy.version, human_message=f"Only {product.stock} units are available."), None
    if proposal.requested_discount_pct > policy.max_discount_pct:
        if (
            policy.exception_approval_allowed
            and policy.review_max_discount_pct is not None
            and proposal.requested_discount_pct <= policy.review_max_discount_pct
        ):
            return GuardDecision(
                decision="REVIEW", reason_code="MANAGER_APPROVAL_REQUIRED",
                policy_id=policy.policy_id, policy_version=policy.version,
                human_message=f"The active {policy.version} policy requires manager approval above {policy.max_discount_pct}% (up to {policy.review_max_discount_pct}%).",
            ), None
        return GuardDecision(decision="DENY", reason_code="DISCOUNT_LIMIT_EXCEEDED", policy_id=policy.policy_id, policy_version=policy.version, human_message=f"The active {policy.version} policy allows at most {policy.max_discount_pct}% discount."), None

    breakdown = calculate_breakdown(product, proposal.quantity, proposal.requested_discount_pct)
    return GuardDecision(
        decision="ALLOW", reason_code="WITHIN_POLICY", policy_id=policy.policy_id,
        policy_version=policy.version, human_message="The quote is within the active policy and can be saved as a draft.",
    ), breakdown
