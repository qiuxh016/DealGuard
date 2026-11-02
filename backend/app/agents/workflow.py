import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Customer, DemoActor, Policy, Product, QuoteDraft
from app.guards.business_rules import evaluate_quote
from app.schemas.deal import ActionProposal, InquiryIntent, PolicyEvidence, TraceEvent


class DealGuardState(TypedDict, total=False):
    run_id: str
    actor: DemoActor
    session: Session
    raw_inquiry: str
    execution_mode: str
    intent: dict[str, Any]
    customer: Customer | None
    product: Product | None
    policy: Policy | None
    evidence: list[dict[str, Any]]
    proposal: dict[str, Any]
    decision: dict[str, Any]
    breakdown: dict[str, Any] | None
    quote: dict[str, Any] | None
    events: list[dict[str, Any]]
    status: str
    stage_durations_ms: dict[str, int]
    usage: dict[str, int]


def _event(step: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return TraceEvent(step=step, status=status, message=message, timestamp=datetime.now(timezone.utc), details=details or {}).model_dump(mode="json")


def _parse_demo_inquiry(raw: str) -> InquiryIntent:
    quantity_match = re.search(r"(?<![-\d])([\d,]+)\s*(?:units?|items?|pieces?)\b", raw, re.IGNORECASE)
    discount_match = re.search(r"(?<![-\d])(\d+(?:\.\d+)?)\s*%", raw)
    product_id = "P-100" if re.search(r"P-100|Product\s*A", raw, re.IGNORECASE) else None
    quantity = int(quantity_match.group(1).replace(",", "")) if quantity_match else None
    if quantity is not None and quantity <= 0:
        quantity = None
    discount = Decimal(discount_match.group(1)) if discount_match else None
    if discount is not None and not Decimal("0") <= discount <= Decimal("100"):
        discount = None
    return InquiryIntent(
        product_id=product_id, quantity=quantity, requested_discount_pct=discount,
        customer_message=raw,
    )


def _parse_intent(state: DealGuardState) -> dict[str, Any]:
    mode = "llm" if settings.llm_api_key and settings.llm_model else "demo"
    if mode == "demo":
        intent = _parse_demo_inquiry(state["raw_inquiry"])
        message = "Inquiry parsed by deterministic demo parser."
    else:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            timeout=20,
            max_retries=0,
        ).with_structured_output(InquiryIntent, include_raw=True)
        parsed = model.invoke(
            "Extract only product_id (P-100 for Product A), positive quantity, and requested discount percent from this customer inquiry. "
            "Do not infer or return customer identity, role, or customer tier. Keep the original inquiry as customer_message.\n\n"
            + state["raw_inquiry"]
        )
        raw = parsed.get("raw") if isinstance(parsed, dict) else None
        intent = parsed.get("parsed") if isinstance(parsed, dict) else parsed
        if not isinstance(intent, InquiryIntent):
            intent = InquiryIntent.model_validate(intent)
        usage_meta = getattr(raw, "usage_metadata", None) or getattr(raw, "response_metadata", {}).get("token_usage", {})
        usage = {
            key: int(value) for key, value in (
                ("input_tokens", usage_meta.get("input_tokens", usage_meta.get("prompt_tokens"))),
                ("output_tokens", usage_meta.get("output_tokens", usage_meta.get("completion_tokens"))),
            ) if value is not None
        }
        message = "Inquiry parsed using the configured OpenAI-compatible model."
    return {
        "intent": intent.model_dump(mode="json"), "execution_mode": mode,
        **({"usage": usage} if mode == "llm" else {}),
        "events": state.get("events", []) + [_event("parse_intent", "completed", message, {"mode": mode})],
    }


def _load_business_data(state: DealGuardState) -> dict[str, Any]:
    db = state["session"]
    actor = state["actor"]
    intent = InquiryIntent.model_validate(state["intent"])
    customer = db.scalar(select(Customer).where(Customer.id == actor.customer_id, Customer.tenant_id == actor.tenant_id)) if actor.customer_id else None
    product = db.scalar(select(Product).where(Product.id == intent.product_id, Product.tenant_id == actor.tenant_id)) if intent.product_id else None
    found = customer is not None and product is not None
    return {
        "customer": customer,
        "product": product,
        "events": state.get("events", []) + [_event(
            "load_business_data", "completed" if found else "needs_input",
            "Loaded customer and product from the demo database." if found else "Customer or product could not be resolved from trusted records.",
            {"customer_id": customer.id if customer else None, "product_id": product.id if product else None},
        )],
    }


def _eligible_policy(row: Policy, now: datetime) -> bool:
    starts = row.effective_from.replace(tzinfo=timezone.utc) if row.effective_from.tzinfo is None else row.effective_from
    ends = row.effective_to
    if ends is not None and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    return starts <= now and (ends is None or now < ends)


def _retrieve_policy(state: DealGuardState) -> dict[str, Any]:
    db = state["session"]
    customer = state.get("customer")
    candidates = []
    if customer is not None:
        now = datetime.now(timezone.utc)
        candidates = [
            row for row in db.scalars(select(Policy).where(Policy.tenant_id == customer.tenant_id, Policy.status == "active", Policy.customer_tier == customer.tier)).all()
            if _eligible_policy(row, now)
        ]
    policy = None
    evidence: list[PolicyEvidence] = []
    mode = "demo"
    if candidates:
        query = state["raw_inquiry"]
        if settings.llm_api_key and settings.embedding_model:
            from langchain_chroma import Chroma
            from langchain_openai import OpenAIEmbeddings

            embeddings = OpenAIEmbeddings(
                model=settings.embedding_model, api_key=settings.llm_api_key,
                base_url=settings.llm_base_url or None,
            )
            index = Chroma.from_texts(
                [row.source for row in candidates],
                embeddings,
                metadatas=[{"policy_row_id": row.id, "policy_id": row.policy_id, "version": row.version} for row in candidates],
            )
            hits = index.similarity_search_with_relevance_scores(query, k=min(3, len(candidates)))
            hit_id = hits[0][0].metadata.get("policy_row_id") if hits else candidates[0].id
            policy = next((row for row in candidates if row.id == hit_id), candidates[0])
            mode = "chroma"
        else:
            policy = candidates[0]
        evidence = [PolicyEvidence(
            policy_id=policy.policy_id, version=policy.version, title=policy.title,
            citation=policy.source, source=f"{policy.policy_id}@{policy.version}", retrieval_mode=mode,
        )]
    event = _event(
        "retrieve_policy", "completed" if policy else "no_policy",
        "Retrieved evidence from the active, effective policy set." if policy else "No active effective policy applies; execution will fail closed.",
        {"policy_version": policy.version if policy else None, "retrieval_mode": mode},
    )
    return {
        "policy": policy, "evidence": [item.model_dump(mode="json") for item in evidence],
        "events": state.get("events", []) + [event],
    }


def _validate(state: DealGuardState) -> dict[str, Any]:
    intent = InquiryIntent.model_validate(state["intent"])
    missing = [name for name in ("product_id", "quantity", "requested_discount_pct") if getattr(intent, name) is None]
    if missing:
        decision = {
            "decision": "NEEDS_INPUT", "reason_code": "MISSING_REQUIRED_FIELDS",
            "policy_id": None, "policy_version": None,
            "human_message": "Please include product, quantity, and requested discount (for example: Product A, 1000 units, 8%). Missing: " + ", ".join(missing),
        }
        return {"decision": decision, "breakdown": None, "status": "needs_input", "events": state.get("events", []) + [_event("validate_proposal", "needs_input", decision["human_message"], {"missing": missing})]}
    customer, product, policy = state.get("customer"), state.get("product"), state.get("policy")
    if customer is None or product is None:
        decision = {"decision": "DENY", "reason_code": "BUSINESS_DATA_NOT_FOUND", "policy_id": None, "policy_version": None, "human_message": "The requested customer or product was not found in trusted business data."}
        return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"])]}
    proposal = ActionProposal(
        customer_id=customer.id, product_id=product.id, quantity=intent.quantity,
        requested_discount_pct=intent.requested_discount_pct,
    )
    decision, breakdown = evaluate_quote(state["actor"], customer, product, policy, proposal)
    return {
        "proposal": proposal.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "breakdown": breakdown.model_dump(mode="json") if breakdown else None,
        "status": "completed",
        "events": state.get("events", []) + [_event("validate_proposal", decision.decision.lower(), decision.human_message, {"reason_code": decision.reason_code, "policy_version": decision.policy_version})],
    }


def _save_quote(state: DealGuardState) -> dict[str, Any]:
    db = state["session"]
    policy = state["policy"]
    product = state["product"]
    breakdown = state["breakdown"]
    proposal = ActionProposal.model_validate(state["proposal"])
    row = QuoteDraft(
        tenant_id=state["actor"].tenant_id,
        owner_actor_id=state["actor"].id, customer_id=proposal.customer_id,
        product_id=proposal.product_id, quantity=proposal.quantity,
        unit_price=product.unit_price, discount_pct=proposal.requested_discount_pct,
        subtotal=breakdown["subtotal"], discount_amount=breakdown["discount_amount"],
        total=breakdown["total"], policy_id=policy.policy_id,
        policy_version=policy.version, status="DRAFT",
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    quote = {
        "id": row.id, "status": "DRAFT", "customer_id": row.customer_id,
        "product_id": row.product_id,
        "breakdown": {
            "currency": product.currency, "unit_price": str(row.unit_price), "quantity": row.quantity,
            "discount_pct": str(row.discount_pct), "subtotal": str(row.subtotal),
            "discount_amount": str(row.discount_amount), "total": str(row.total),
        },
        "policy_id": row.policy_id, "policy_version": row.policy_version,
        "created_at": row.created_at.isoformat(),
    }
    return {
        "quote": quote,
        "events": state.get("events", []) + [_event("create_quote_draft", "completed", "Created a quote draft after ALLOW.", {"quote_id": row.id})],
    }


def _route_after_validation(state: DealGuardState) -> str:
    return "save_quote" if state["decision"]["decision"] == "ALLOW" else END


def build_workflow():
    graph = StateGraph(DealGuardState)
    def timed(name: str, fn):
        def run(state):
            started = time.perf_counter()
            update = fn(state)
            elapsed = round((time.perf_counter() - started) * 1000)
            events = update.get("events", [])
            if events:
                events[-1].setdefault("details", {})["duration_ms"] = elapsed
            return {**update, "stage_durations_ms": {**state.get("stage_durations_ms", {}), name: elapsed}}
        return run

    graph.add_node("parse_intent", timed("parse_intent", _parse_intent))
    graph.add_node("load_business_data", timed("load_business_data", _load_business_data))
    graph.add_node("retrieve_policy", timed("retrieve_policy", _retrieve_policy))
    graph.add_node("validate_proposal", timed("validate_proposal", _validate))
    graph.add_node("save_quote", timed("save_quote", _save_quote))
    graph.add_edge(START, "parse_intent")
    graph.add_edge("parse_intent", "load_business_data")
    graph.add_edge("load_business_data", "retrieve_policy")
    graph.add_edge("retrieve_policy", "validate_proposal")
    graph.add_conditional_edges("validate_proposal", _route_after_validation)
    graph.add_edge("save_quote", END)
    return graph.compile()
