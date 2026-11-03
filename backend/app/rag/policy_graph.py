"""Build a deterministic conflict graph from already eligible policy records."""
from datetime import datetime, timezone
from typing import Any


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def policy_conflict_graph(policies: list[Any]) -> dict[str, list[dict[str, Any]]]:
    nodes = []
    for policy in sorted(policies, key=lambda item: (item.policy_id, item.version, item.id)):
        nodes.append({
            "ref": f"{policy.policy_id}@{policy.version}",
            "action_type": policy.action_type,
            "customer_tier": policy.customer_tier,
            "scope": policy.policy_scope,
            "scope_product_id": policy.scope_product_id,
            "effective_from": _utc(policy.effective_from).isoformat(),
            "effective_to": _utc(policy.effective_to).isoformat() if policy.effective_to else None,
        })

    groups: dict[tuple[Any, ...], list[Any]] = {}
    for policy in policies:
        key = (policy.tenant_id, policy.action_type, policy.customer_tier, policy.policy_scope, policy.scope_product_id)
        groups.setdefault(key, []).append(policy)

    edges = []
    for group in groups.values():
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                start = max(_utc(left.effective_from), _utc(right.effective_from))
                left_end = _utc(left.effective_to) if left.effective_to else None
                right_end = _utc(right.effective_to) if right.effective_to else None
                overlap = (left_end is None or start < left_end) and (right_end is None or start < right_end)
                left_rule = (left.max_discount_pct, left.review_max_discount_pct, left.exception_approval_allowed)
                right_rule = (right.max_discount_pct, right.review_max_discount_pct, right.exception_approval_allowed)
                multiple_active_versions = left.policy_id == right.policy_id and left.version != right.version
                if not overlap or (left_rule == right_rule and not multiple_active_versions):
                    continue
                refs = sorted((f"{left.policy_id}@{left.version}", f"{right.policy_id}@{right.version}"))
                reason = "MULTIPLE_ACTIVE_VERSIONS" if multiple_active_versions else "OVERLAPPING_RULES"
                edges.append({"type": "conflicts_with", "from": refs[0], "to": refs[1], "reason": reason})
    return {"nodes": nodes, "edges": sorted(edges, key=lambda edge: (edge["from"], edge["to"]))}
