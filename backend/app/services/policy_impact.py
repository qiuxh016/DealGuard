"""Read-only policy change simulation over saved inquiry runs."""

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.workflow import build_workflow
from app.db.models import Customer, DemoActor, Policy, PolicyDraft, Product, RunRecord
from app.guards.business_rules import evaluate_quote_with_trace
from app.schemas.deal import ActionProposal


def _candidate_policy(draft: PolicyDraft) -> Policy:
    return Policy(
        id=f"impact:{draft.id}", policy_id=draft.policy_id, version=draft.version,
        title=draft.title, action_type=draft.action_type or "reference_only",
        policy_scope=draft.policy_scope, scope_product_id=draft.scope_product_id,
        status="active", customer_tier=draft.customer_tier,
        max_discount_pct=draft.max_discount_pct, review_max_discount_pct=draft.review_max_discount_pct,
        exception_approval_allowed=draft.exception_approval_allowed,
        effective_from=draft.created_at, effective_to=None, source=draft.source,
        tenant_id=draft.tenant_id,
    )


def _stored_policy(value: dict[str, Any] | None, tenant_id: str) -> Policy | None:
    if not value:
        return None
    payload = dict(value)
    for field in ("max_discount_pct", "review_max_discount_pct"):
        if payload.get(field) is not None:
            payload[field] = Decimal(str(payload[field]))
    for field in ("effective_from", "effective_to"):
        if payload.get(field):
            payload[field] = datetime.fromisoformat(payload[field])
    return Policy(
        id=f"snapshot:{value['policy_id']}:{value['version']}",
        **payload,
        status="active", tenant_id=tenant_id,
    )


def _evaluate_snapshot(snapshot: dict[str, Any], policy: Policy | None) -> dict[str, Any]:
    actor_data = snapshot["actor"]
    actor = DemoActor(
        id=actor_data["id"], display_name=actor_data["id"], role=actor_data["role"],
        customer_id=actor_data.get("customer_id"), tenant_id=snapshot["tenant_id"],
    )
    customer = Customer(
        id=snapshot["customer"]["id"], name=snapshot["customer"]["id"],
        tier=snapshot["customer"]["tier"], tenant_id=snapshot["tenant_id"],
    )
    line_decisions = []
    totals = []
    for line in snapshot["lines"]:
        product_data, proposal_data = line["product"], line["proposal"]
        product = Product(
            id=product_data["id"], name=product_data.get("name") or product_data["id"],
            currency=product_data.get("currency") or "USD",
            unit_price=Decimal(str(product_data["unit_price"])),
            stock=int(product_data["stock"]), tenant_id=snapshot["tenant_id"],
        )
        proposal = ActionProposal(
            customer_id=customer.id, product_id=product.id,
            quantity=int(proposal_data["quantity"]),
            requested_discount_pct=Decimal(str(proposal_data["requested_discount_pct"])),
        )
        decision, breakdown, _checks = evaluate_quote_with_trace(
            actor, customer, product, policy, proposal, snapshot.get("policy_scope", "general"),
        )
        line_decisions.append(decision)
        totals.append(str(breakdown.total) if breakdown else None)
    denied = next((item for item in line_decisions if item.decision == "DENY"), None)
    review = next((item for item in line_decisions if item.decision == "REVIEW"), None)
    chosen = denied or review or (line_decisions[0] if line_decisions else None)
    return {
        "decision": chosen.decision if chosen else "DENY",
        "reason_code": chosen.reason_code if chosen else "IMPACT_SNAPSHOT_EMPTY",
        "totals": totals,
        "lines": [item.model_dump(mode="json") for item in line_decisions],
    }


def _decision_diff(run_id: str, baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    changed = baseline["decision"] != candidate["decision"] or baseline["reason_code"] != candidate["reason_code"] or baseline["totals"] != candidate["totals"]
    return {
        "run_id": run_id, "status": "replayed",
        "baseline_decision": baseline["decision"], "candidate_decision": candidate["decision"],
        "baseline_reason_code": baseline["reason_code"], "candidate_reason_code": candidate["reason_code"],
        "changed": changed, "details": {"baseline_totals": baseline["totals"], "candidate_totals": candidate["totals"]},
    }


def _e2e_run(db: Session, run: RunRecord, actor: DemoActor, candidate: Policy | None) -> dict[str, Any]:
    snapshot = (run.result or {}).get("_impact_snapshot") or {}
    selected_customer = (snapshot.get("customer") or {}).get("id")
    if actor.role != "admin" and selected_customer != actor.customer_id:
        return {"run_id": run.id, "status": "skipped", "skip_reason": "ACTOR_ASSIGNMENT_CHANGED"}
    nested = db.begin_nested()
    try:
        initial = {
            "run_id": f"impact:{run.id}", "actor": actor, "session": db,
            "raw_inquiry": run.inquiry, "customer_id": selected_customer,
            "execution_mode": "demo", "events": [], "status": "completed",
        }
        if candidate is not None:
            initial["policy_override"] = candidate
        result = build_workflow().invoke(initial)
        nested.rollback()
        decision = result.get("decision") or {}
        return {
            "run_id": run.id, "status": "replayed",
            "decision": decision.get("decision", "DENY"),
            "reason_code": decision.get("reason_code", "WORKFLOW_FAILED"),
            "execution_mode": result.get("execution_mode", "demo"),
            "retrieval_mode": next((event.get("details", {}).get("retrieval_mode") for event in result.get("events", []) if event.get("step") == "retrieve_policy"), None),
        }
    except Exception:
        nested.rollback()
        return {"run_id": run.id, "status": "failed", "skip_reason": "REPLAY_FAILED"}


def simulate_policy_impact(
    db: Session, tenant_id: str, draft: PolicyDraft, run_ids: list[str] | None, mode: str,
) -> dict[str, Any]:
    query = select(RunRecord).where(RunRecord.tenant_id == tenant_id).order_by(RunRecord.created_at.desc())
    if run_ids is not None:
        query = query.where(RunRecord.id.in_(run_ids))
    rows = db.scalars(query.limit(50)).all()
    candidate = _candidate_policy(draft)
    cases = []
    if mode == "end_to_end_replay":
        for run in rows:
            actor = db.get(DemoActor, run.actor_id)
            if actor is None or actor.tenant_id != tenant_id:
                cases.append({"run_id": run.id, "status": "skipped", "skip_reason": "ACTOR_NOT_FOUND"})
                continue
            baseline = _e2e_run(db, run, actor, None)
            proposed = _e2e_run(db, run, actor, candidate)
            if baseline["status"] != "replayed" or proposed["status"] != "replayed":
                cases.append({"run_id": run.id, "status": "failed", "skip_reason": "REPLAY_FAILED"})
                continue
            changed = baseline["decision"] != proposed["decision"] or baseline["reason_code"] != proposed["reason_code"]
            cases.append({
                "run_id": run.id, "status": "replayed",
                "baseline_decision": baseline["decision"], "candidate_decision": proposed["decision"],
                "baseline_reason_code": baseline["reason_code"], "candidate_reason_code": proposed["reason_code"],
                "changed": changed,
                "details": {"baseline_retrieval_mode": baseline.get("retrieval_mode"), "candidate_retrieval_mode": proposed.get("retrieval_mode"), "candidate_execution_mode": proposed.get("execution_mode")},
            })
    else:
        for run in rows:
            snapshot = (run.result or {}).get("_impact_snapshot")
            if not snapshot or not snapshot.get("lines"):
                cases.append({"run_id": run.id, "status": "skipped", "skip_reason": "FROZEN_SNAPSHOT_UNAVAILABLE"})
                continue
            baseline_policy = _stored_policy(snapshot.get("policy"), tenant_id)
            baseline = _evaluate_snapshot(snapshot, baseline_policy)
            proposed = _evaluate_snapshot(snapshot, candidate)
            cases.append(_decision_diff(run.id, baseline, proposed))

    replayed = [case for case in cases if case["status"] == "replayed"]
    changed = [case for case in replayed if case.get("changed")]
    transitions = Counter(f"{case['baseline_decision']}->{case['candidate_decision']}" for case in changed)
    return {
        "mode": mode, "policy_id": draft.policy_id, "candidate_version": draft.version,
        "total_cases": len(cases), "replayed_cases": len(replayed),
        "skipped_cases": sum(case["status"] == "skipped" for case in cases),
        "changed_cases": len(changed),
        "change_rate": Decimal(len(changed)) / Decimal(len(replayed)) if replayed else None,
        "transitions": dict(transitions), "cases": cases, "writes_created": False,
    }
