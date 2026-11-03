"""Audit evidence that accompanies, but never determines, Guard authorization."""
from hashlib import sha256
from typing import Any

from app.db.models import Policy
from app.schemas.deal import GuardDecision


def policy_source_hash(policy: Policy) -> str:
    return sha256(policy.source.encode("utf-8")).hexdigest()


def quote_provenance(
    policy: Policy,
    decision: GuardDecision,
    checks: list[dict[str, str]],
    citation: str | None,
    citation_ref: str | None,
    chunk_id: str | None,
) -> dict[str, Any]:
    return {
        "citation": citation,
        "citation_ref": citation_ref,
        "chunk_id": chunk_id,
        "policy_source_hash": policy_source_hash(policy),
        "authorization_rule": {
            "action_type": policy.action_type,
            "customer_tier": policy.customer_tier,
            "policy_scope": policy.policy_scope,
            "scope_product_id": policy.scope_product_id,
            "max_discount_pct": str(policy.max_discount_pct) if policy.max_discount_pct is not None else None,
            "review_max_discount_pct": str(policy.review_max_discount_pct) if policy.review_max_discount_pct is not None else None,
            "exception_approval_allowed": policy.exception_approval_allowed,
        },
        "guard_evaluation": {
            "decision": decision.decision,
            "reason_code": decision.reason_code,
            "checks": checks,
        },
    }
