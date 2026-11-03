from decimal import Decimal, ROUND_HALF_UP

from app.db.models import Customer, DemoActor, Policy, Product
from app.schemas.deal import ActionProposal, GuardDecision, QuoteBreakdown
from app.services.permissions import has_capability

CENT = Decimal("0.01")


def policy_scope_applies(policy: Policy, policy_scope: str | None, product: Product) -> bool:
    if policy.policy_scope == "general":
        return True
    if policy_scope != policy.policy_scope:
        return False
    return policy.policy_scope != "product_specific" or policy.scope_product_id == product.id


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
    policy_scope: str | None = "general",
) -> tuple[GuardDecision, QuoteBreakdown | None]:
    decision, breakdown, _ = evaluate_quote_with_trace(actor, customer, product, policy, proposal, policy_scope)
    return decision, breakdown


def evaluate_quote_with_trace(
    actor: DemoActor,
    customer: Customer,
    product: Product,
    policy: Policy | None,
    proposal: ActionProposal,
    policy_scope: str | None = "general",
) -> tuple[GuardDecision, QuoteBreakdown | None, list[dict[str, str]]]:
    checks: list[dict[str, str]] = []

    def record(name: str, passed: bool) -> bool:
        checks.append({"check": name, "result": "PASS" if passed else "FAIL"})
        return passed

    if not record("sales_capability", has_capability(actor, "inquiry.run")):
        return GuardDecision(decision="DENY", reason_code="SALES_ROLE_REQUIRED", human_message="A sales representative account is required to prepare a quote."), None, checks
    if not record("customer_ownership", actor.role == "admin" or actor.customer_id == customer.id):
        return GuardDecision(decision="DENY", reason_code="RESOURCE_NOT_OWNED", human_message="This account cannot quote for the selected customer."), None, checks
    if policy is not None and not record("policy_action", policy.action_type == "quote_discount"):
        return GuardDecision(decision="DENY", reason_code="POLICY_ACTION_MISMATCH", policy_id=policy.policy_id, policy_version=policy.version,
                             human_message="This policy does not authorize discount quotes."), None, checks
    if policy is not None and not record("policy_scope", policy_scope_applies(policy, policy_scope, product)):
        return GuardDecision(decision="DENY", reason_code="POLICY_SCOPE_MISMATCH", policy_id=policy.policy_id, policy_version=policy.version,
                             human_message="The selected policy scope does not apply to this inquiry and product."), None, checks
    if not record("customer_tier", customer.tier == "VIP" and (policy is None or customer.tier == policy.customer_tier)) or policy is None:
        return GuardDecision(decision="DENY", reason_code="NO_APPLICABLE_POLICY", human_message="No active discount policy applies to this verified customer tier."), None, checks
    if not record("policy_rule_complete", policy.max_discount_pct is not None):
        return GuardDecision(decision="DENY", reason_code="POLICY_RULES_INCOMPLETE", policy_id=policy.policy_id, policy_version=policy.version,
                             human_message="The active discount policy has no structured discount limit."), None, checks
    if not record("quantity", proposal.quantity > 0):
        return GuardDecision(decision="DENY", reason_code="INVALID_QUANTITY", policy_id=policy.policy_id, policy_version=policy.version, human_message="Quantity must be greater than zero."), None, checks
    if not record("inventory", proposal.quantity <= product.stock):
        return GuardDecision(decision="DENY", reason_code="INSUFFICIENT_STOCK", policy_id=policy.policy_id, policy_version=policy.version, human_message=f"Only {product.stock} units are available."), None, checks
    if proposal.requested_discount_pct > policy.max_discount_pct:
        needs_review = (
            policy.exception_approval_allowed
            and policy.review_max_discount_pct is not None
            and proposal.requested_discount_pct <= policy.review_max_discount_pct
        )
        checks.append({"check": "discount_limit", "result": "REVIEW" if needs_review else "FAIL"})
        if needs_review:
            return GuardDecision(
                decision="REVIEW", reason_code="MANAGER_APPROVAL_REQUIRED",
                policy_id=policy.policy_id, policy_version=policy.version,
                human_message=f"The active {policy.version} policy requires manager approval above {policy.max_discount_pct}% (up to {policy.review_max_discount_pct}%).",
            ), None, checks
        return GuardDecision(decision="DENY", reason_code="DISCOUNT_LIMIT_EXCEEDED", policy_id=policy.policy_id, policy_version=policy.version, human_message=f"The active {policy.version} policy allows at most {policy.max_discount_pct}% discount.", suggested_discount_pct=policy.max_discount_pct, suggested_product_id=product.id), None, checks

    checks.append({"check": "discount_limit", "result": "PASS"})
    breakdown = calculate_breakdown(product, proposal.quantity, proposal.requested_discount_pct)
    checks.append({"check": "quote_arithmetic", "result": "PASS"})
    return GuardDecision(
        decision="ALLOW", reason_code="WITHIN_POLICY", policy_id=policy.policy_id,
        policy_version=policy.version, human_message="The quote is within the active policy and can be saved as a draft.",
    ), breakdown, checks
