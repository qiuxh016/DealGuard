from datetime import datetime, timezone
from decimal import Decimal
import re
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Customer, DemoActor, Policy, Product, QuoteDraft
from app.guards.business_rules import evaluate_quote_with_trace
from app.schemas.deal import ActionProposal, InquiryIntent, PolicyEvidence, QuoteItemIntent, TraceEvent
from app.rag.provenance import quote_provenance
from app.services.inventory import aggregate_inventory_failure


class WorkflowStepFailure(RuntimeError):
    def __init__(self, step: str, reason_code: str):
        super().__init__(reason_code)
        self.step = step
        self.reason_code = reason_code


def _llm_failure_code(error: Exception) -> str:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403}:
        return "LLM_CREDENTIALS_INVALID"
    if status_code == 429 or isinstance(status_code, int) and status_code >= 500:
        return "LLM_PROVIDER_UNAVAILABLE"
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return "LLM_REQUEST_INVALID"
    error_type = type(error).__name__.lower()
    if "timeout" in error_type:
        return "LLM_PROVIDER_TIMEOUT"
    if "connection" in error_type:
        return "LLM_PROVIDER_UNAVAILABLE"
    return "LLM_REQUEST_FAILED"


class DealGuardState(TypedDict, total=False):
    run_id: str
    actor: DemoActor
    session: Session
    raw_inquiry: str
    customer_id: str
    execution_mode: str
    intent: dict[str, Any]
    customer: Customer | None
    product: Product | None
    products: list[Product]
    policy: Policy | None
    policy_override: Policy | None
    evidence: list[dict[str, Any]]
    retrieval_ambiguous: bool
    evidence_low_relevance: bool
    policy_conflict: bool
    policy_conflict_edges: list[dict[str, Any]]
    proposal: dict[str, Any]
    decision: dict[str, Any]
    breakdown: dict[str, Any] | None
    line_results: list[dict[str, Any]]
    quote: dict[str, Any] | None
    events: list[dict[str, Any]]
    status: str
    stage_durations_ms: dict[str, int]
    usage: dict[str, int]
    scope_ambiguous: bool
    scope_not_found: bool


_POLICY_SCOPE_PATTERNS = {
    "bulk_order": re.compile(r"\b(?:bulk|volume|wholesale|pallet|case[- ]lot|high[- ]volume|large[- ]quantity|large lot|large purchase|quantity discount|distributor)\b|批量|大批量|大宗|批发|整托|整箱|大批采购", re.IGNORECASE),
    "loyalty": re.compile(r"\b(?:loyalty|loyal customer|loyal account|repeat buyer|repeat customer|repeat-customer|returning customer|long[- ]standing|member reward|membership discount|retention|renewal|relationship discount|relationship reward|established account|reorder|re-order)\b|忠诚客户|忠诚度|老客户|回头客|长期合作|会员回馈|复购|续购", re.IGNORECASE),
    "early_payment": re.compile(r"\b(?:early payment|paid early|paying upfront|upfront in cash|prepay(?:ment)?|upfront payment|advance payment|advance settlement|prompt payment|settle.{0,30}(?:invoice|bill)|clear.{0,20}(?:invoice|bill)|cash[- ]before[- ](?:delivery|shipment)|cash payment|immediate prepayment|payment in advance|pay the bill promptly)\b|提前付款|提前支付|预付款|预付|及时付款|及时结款|七天内结清|一周内结清|现金付款", re.IGNORECASE),
    "seasonal_campaign": re.compile(r"\b(?:seasonal|holiday|spring sale|summer sale|year[- ]end|festive|campaign|event offer)\b|季节|节日|春季促销|夏季促销|年末活动|节庆|活动促销|活动优惠", re.IGNORECASE),
    "product_specific": re.compile(r"\b(?:product[- ]specific|product category|product family|product line|eligible model|designated range|selected product|specified product|sku[- ]specific|catalog item|category promotion|category discount|series[- ]specific|this model)\b|指定商品|商品类别|产品类别|品类促销|产品系列|产品线|型号折扣|指定型号", re.IGNORECASE),
}


def _infer_policy_scope(inquiry: str) -> str | None:
    matches = [scope for scope, pattern in _POLICY_SCOPE_PATTERNS.items() if pattern.search(inquiry)]
    if not matches:
        return "general"
    return matches[0] if len(matches) == 1 else None


def _recover_safe_intent(raw: Any, inquiry: str) -> InquiryIntent | None:
    """Keep only schema-safe fields from a malformed tool call; invalid values become missing."""
    calls = getattr(raw, "tool_calls", None) or []
    arguments = next((call.get("args") for call in calls if isinstance(call.get("args"), dict)), None)
    if arguments is None:
        return None
    payload = dict(arguments)
    payload["customer_message"] = inquiry
    payload["policy_scope"] = _infer_policy_scope(inquiry)
    raw_items = payload.get("items")
    if raw_items:
        # A malformed single line can safely fall back to the legacy fields so
        # valid values remain available for a follow-up question. Multi-line
        # requests stay atomic: one bad line invalidates the entire list.
        if len(raw_items) == 1 and isinstance(raw_items[0], dict):
            item = raw_items[0]
            quantity = item.get("quantity")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
                quantity = None
            discount = item.get("requested_discount_pct")
            try:
                discount_value = Decimal(str(discount)) if discount is not None else None
            except Exception:
                discount_value = None
            if discount_value is not None and not Decimal("0") <= discount_value <= Decimal("100"):
                discount_value = None
            product_id = item.get("product_id")
            payload.update(
                items=[],
                product_id=product_id if isinstance(product_id, str) and product_id.strip() else None,
                quantity=quantity,
                requested_discount_pct=str(discount_value) if discount_value is not None else None,
            )
            try:
                return InquiryIntent.model_validate(payload)
            except ValidationError:
                return None
        safe_items = []
        for item in raw_items:
            if not isinstance(item, dict):
                payload["items"] = []
                payload.update(product_id=None, quantity=None, requested_discount_pct=None)
                return InquiryIntent.model_validate(payload)
            quantity = item.get("quantity")
            discount = item.get("requested_discount_pct")
            try:
                discount_value = Decimal(str(discount)) if discount is not None else None
            except Exception:
                discount_value = None
            if not item.get("product_id") or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0 or discount_value is None or not Decimal("0") <= discount_value <= Decimal("100"):
                payload["items"] = []
                payload.update(product_id=None, quantity=None, requested_discount_pct=None)
                return InquiryIntent.model_validate(payload)
            safe_items.append({**item, "requested_discount_pct": str(discount_value)})
        payload["items"] = safe_items
    else:
        payload["items"] = []
        quantity = payload.get("quantity")
        if quantity is not None and (not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0):
            payload["quantity"] = None
        discount = payload.get("requested_discount_pct")
        try:
            discount_value = Decimal(str(discount)) if discount is not None else None
        except Exception:
            discount_value = None
        if discount is not None and (discount_value is None or not Decimal("0") <= discount_value <= Decimal("100")):
            payload["requested_discount_pct"] = None
    try:
        return InquiryIntent.model_validate(payload)
    except ValidationError:
        return None


def _event(step: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return TraceEvent(step=step, status=status, message=message, timestamp=datetime.now(timezone.utc), details=details or {}).model_dump(mode="json")


def _parse_demo_inquiry(raw: str) -> InquiryIntent:
    policy_scope = _infer_policy_scope(raw)
    item_matches = re.findall(r"(P-[A-Z0-9_-]+)\s*[:,-]?\s*([\d,]+)\s*(?:units?|pieces?)\s*(?:at\s*)?([\d.]+)\s*%", raw, re.IGNORECASE)
    if len(item_matches) > 1:
        items = [QuoteItemIntent(product_id=pid.upper(), quantity=int(qty.replace(",", "")), requested_discount_pct=Decimal(discount)) for pid, qty, discount in item_matches]
        return InquiryIntent(items=items, policy_scope=policy_scope, customer_message=raw)
    quantity_match = re.search(r"(?<![-\d])([\d,]+)\s*-?\s*(?:units?|items?|pieces?|products?)\b", raw, re.IGNORECASE)
    discount_match = re.search(r"(?<![-\d])(\d+(?:\.\d+)?)\s*%", raw)
    product_match = re.search(r"\b(P-[A-Z0-9_-]+)\b", raw, re.IGNORECASE)
    product_id = product_match.group(1).upper() if product_match else "P-100" if re.search(r"Product\s*A", raw, re.IGNORECASE) else None
    quantity = int(quantity_match.group(1).replace(",", "")) if quantity_match else None
    if quantity is not None and quantity <= 0:
        quantity = None
    discount = Decimal(discount_match.group(1)) if discount_match else None
    if discount is not None and not Decimal("0") <= discount <= Decimal("100"):
        discount = None
    return InquiryIntent(
        product_id=product_id, quantity=quantity, requested_discount_pct=discount,
        policy_scope=policy_scope, customer_message=raw,
    )


def _parse_intent(state: DealGuardState) -> dict[str, Any]:
    mode = "llm" if settings.llm_api_key and settings.llm_model else "demo"
    if mode == "demo":
        intent = _parse_demo_inquiry(state["raw_inquiry"])
        message = "Inquiry parsed by deterministic demo parser."
        event_status = "completed"
    else:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            timeout=20,
            max_retries=1,
            extra_body={"thinking": {"type": "disabled"}},
        ).with_structured_output(InquiryIntent, method="function_calling", include_raw=True)
        try:
            parsed = model.invoke(
                "Extract either legacy single product fields or an items list with product_id, positive quantity, and requested discount percent. "
                "Copy explicit product IDs exactly; map the demo catalog names Product A to P-100 and Product B to P-200. "
                "For a single item, set any missing field to null. Never default a missing discount to zero. "
                "Extract policy_scope only from explicit inquiry wording; use one of general, bulk_order, loyalty, early_payment, seasonal_campaign, product_specific. "
                "Use general when no specific discount program is mentioned; do not infer a special program from product/customer identity alone. "
                "Set quantity to null when it is zero or negative; set discount to null when it is below 0 or above 100. "
                "Do not clamp, round, or otherwise change a number. For a multi-item request, return an empty items list if any line has a missing or invalid quantity or discount. "
                "Do not infer or return customer identity, role, or customer tier. Keep the original inquiry as customer_message.\n\n"
                + state["raw_inquiry"]
            )
        except Exception as error:
            raise WorkflowStepFailure("parse_intent", _llm_failure_code(error)) from error
        raw = parsed.get("raw") if isinstance(parsed, dict) else None
        intent = parsed.get("parsed") if isinstance(parsed, dict) else parsed
        parsing_error = parsed.get("parsing_error") if isinstance(parsed, dict) else None
        event_status = "completed"
        if parsing_error is not None or intent is None:
            intent = _recover_safe_intent(raw, state["raw_inquiry"])
            if intent is None:
                raise WorkflowStepFailure("parse_intent", "LLM_INVALID_OUTPUT")
            event_status = "invalid_output_sanitized"
            message = "The model returned invalid fields; unsafe values were treated as missing."
        elif not isinstance(intent, InquiryIntent):
            try:
                intent = InquiryIntent.model_validate(intent)
            except ValidationError:
                intent = _recover_safe_intent(raw, state["raw_inquiry"])
                if intent is None:
                    raise WorkflowStepFailure("parse_intent", "LLM_INVALID_OUTPUT")
                event_status = "invalid_output_sanitized"
                message = "The model returned invalid fields; unsafe values were treated as missing."
        usage_meta = getattr(raw, "usage_metadata", None) or getattr(raw, "response_metadata", {}).get("token_usage", {})
        usage = {
            key: int(value) for key, value in (
                ("input_tokens", usage_meta.get("input_tokens", usage_meta.get("prompt_tokens"))),
                ("output_tokens", usage_meta.get("output_tokens", usage_meta.get("completion_tokens"))),
            ) if value is not None
        }
        if event_status == "completed":
            message = "Inquiry parsed using the configured OpenAI-compatible model."
        intent.policy_scope = _infer_policy_scope(state["raw_inquiry"])
    return {
        "intent": intent.model_dump(mode="json"), "execution_mode": mode,
        **({"usage": usage} if mode == "llm" else {}),
        "events": state.get("events", []) + [_event("parse_intent", event_status, message, {"mode": mode})],
    }


def _load_business_data(state: DealGuardState) -> dict[str, Any]:
    db = state["session"]
    actor = state["actor"]
    intent = InquiryIntent.model_validate(state["intent"])
    customer_id = state.get("customer_id") or actor.customer_id
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id)) if customer_id else None
    if intent.items:
        products_by_id = {row.id: row for row in db.scalars(select(Product).where(Product.id.in_([item.product_id for item in intent.items]), Product.tenant_id == actor.tenant_id)).all()}
        products = [products_by_id[item.product_id] for item in intent.items if item.product_id in products_by_id]
        product = products[0] if len(products) == len(intent.items) else None
    else:
        product = db.scalar(select(Product).where(Product.id == intent.product_id, Product.tenant_id == actor.tenant_id)) if intent.product_id else None
        products = [product] if product else []
    found = customer is not None and product is not None
    return {
        "customer": customer,
        "product": product,
        "products": products,
        "events": state.get("events", []) + [_event(
            "load_business_data", "completed" if found else "needs_input",
            "Loaded customer and product from tenant business records." if found else "Customer or product could not be resolved from trusted records.",
            {"customer_id": customer.id if customer else None, "requested_customer_id": customer_id, "tenant_id": actor.tenant_id, "product_id": product.id if product else None},
        )],
    }


def _retrieve_policy(state: DealGuardState) -> dict[str, Any]:
    db = state["session"]
    customer = state.get("customer")
    all_candidates = []
    if customer is not None:
        now = datetime.now(timezone.utc)
        from app.services.policies import current_quote_policies
        all_candidates = current_quote_policies(db, customer.tenant_id, customer.tier, now=now)
        override = state.get("policy_override")
        if override is not None and override.tenant_id == customer.tenant_id and override.customer_tier == customer.tier:
            all_candidates = [row for row in all_candidates if row.policy_id != override.policy_id]
            if override.action_type == "quote_discount":
                all_candidates.append(override)
    intent = InquiryIntent.model_validate(state.get("intent") or _parse_demo_inquiry(state["raw_inquiry"]).model_dump(mode="json"))
    scope_ambiguous = intent.policy_scope is None
    candidates = []
    if not scope_ambiguous:
        if intent.policy_scope == "general":
            candidates = [row for row in all_candidates if row.policy_scope == "general"]
        else:
            product_ids = {item.product_id for item in intent.items} if intent.items else {intent.product_id}
            scoped = [
                row for row in all_candidates
                if row.policy_scope == intent.policy_scope
                and (row.policy_scope != "product_specific" or row.scope_product_id in product_ids)
            ]
            candidates = scoped or [row for row in all_candidates if row.policy_scope == "general"]
    scope_not_found = bool(all_candidates) and not candidates and not scope_ambiguous
    from app.rag.policy_graph import policy_conflict_graph
    policy_graph = policy_conflict_graph(candidates)
    conflict_edges = policy_graph["edges"]
    policy_conflict = bool(conflict_edges)
    policy = None
    evidence: list[PolicyEvidence] = []
    query_expansion_terms: list[str] = []
    mode = "demo"
    retrieval_ambiguous = False
    evidence_low_relevance = False
    embedding_error = False
    retrieval_diagnostics: dict[str, Any] = {}
    if candidates:
        query = state["raw_inquiry"]
        from app.rag.policy_chunks import policy_clauses

        clauses_by_policy = {
            row.policy_id: policy_clauses(row.policy_id, row.version, row.source)
            for row in candidates
        }
        clause_by_id = {
            clause["chunk_id"]: (row, clause)
            for row in candidates
            for clause in clauses_by_policy[row.policy_id]
        }
        clause_sources = {chunk_id: clause["text"] for chunk_id, (_, clause) in clause_by_id.items()}
        selected_clause = None
        if policy_conflict:
            mode = "conflict_graph_abstain"
        elif len(candidates) == 1:
            from app.rag.evaluation import _bm25_scores, _bilingual_query_matches
            clause_ids = sorted(clause_sources)
            clause_scores = _bm25_scores(query, clause_ids, clause_sources, include_cjk=True)
            ranked_clauses = sorted(clause_scores, key=lambda chunk_id: (-clause_scores[chunk_id], chunk_id))
            query_expansion_terms = _bilingual_query_matches(query)
            if not ranked_clauses or clause_scores[ranked_clauses[0]] <= 0:
                evidence_low_relevance = True
                mode = "registry_abstain"
            else:
                policy = candidates[0]
                mode = "registry"
                if settings.local_embedding_model_path:
                    try:
                        from app.rag.embeddings import LocalPolicyEmbeddings
                        from app.rag.evaluation import _rrf_rank

                        embeddings = LocalPolicyEmbeddings(settings.local_embedding_model_path)
                        document_vectors = embeddings.embed_documents([clause_sources[key] for key in clause_ids])
                        query_vector = embeddings.embed_query(query)
                        dense_scores = {
                            key: sum(a * b for a, b in zip(query_vector, vector, strict=True))
                            for key, vector in zip(clause_ids, document_vectors, strict=True)
                        }
                        dense_order = sorted(clause_ids, key=lambda key: (-dense_scores[key], key))
                        fused = _rrf_rank([ranked_clauses, dense_order])
                        selected_clause = clause_by_id[fused[0]][1]
                        mode = "chroma_local"
                    except Exception:
                        embedding_error = True
                        selected_clause = clause_by_id[ranked_clauses[0]][1]
                else:
                    selected_clause = clause_by_id[ranked_clauses[0]][1]
        else:
            # Candidate eligibility is already fixed by Registry metadata. Hybrid
            # ranking may select evidence, but only BM25/Dense agreement may pick
            # a policy; Guard still authorizes from structured policy fields.
            if not settings.local_embedding_model_path:
                retrieval_ambiguous = True
                mode = "registry_abstain"
                retrieval_diagnostics["abstain_reason"] = "LOCAL_EMBEDDING_NOT_CONFIGURED"
            else:
                try:
                    from app.rag.embeddings import LocalPolicyEmbeddings
                    from app.rag.evaluation import _bm25_scores, _bilingual_query_matches, _rrf_rank

                    clause_ids = sorted(clause_sources)
                    bm25_clause_scores = _bm25_scores(query, clause_ids, clause_sources, include_cjk=True)
                    embeddings = LocalPolicyEmbeddings(settings.local_embedding_model_path)
                    document_vectors = embeddings.embed_documents([clause_sources[key] for key in clause_ids])
                    query_vector = embeddings.embed_query(query)
                    dense_clause_scores = {
                        key: sum(a * b for a, b in zip(query_vector, vector, strict=True))
                        for key, vector in zip(clause_ids, document_vectors, strict=True)
                    }
                    policy_bm25 = {
                        row.policy_id: max(bm25_clause_scores[str(clause["chunk_id"])] for clause in clauses_by_policy[row.policy_id])
                        for row in candidates
                    }
                    policy_dense = {
                        row.policy_id: max(dense_clause_scores[str(clause["chunk_id"])] for clause in clauses_by_policy[row.policy_id])
                        for row in candidates
                    }
                    bm25_order = sorted(policy_bm25, key=lambda policy_id: (-policy_bm25[policy_id], policy_id))
                    dense_order = sorted(policy_dense, key=lambda policy_id: (-policy_dense[policy_id], policy_id))
                    bm25_unique = len(bm25_order) == 1 or policy_bm25[bm25_order[0]] > policy_bm25[bm25_order[1]]
                    dense_unique = len(dense_order) == 1 or policy_dense[dense_order[0]] - policy_dense[dense_order[1]] > 1e-8
                    retrieval_diagnostics = {
                        "bm25_top_policy_id": bm25_order[0],
                        "dense_top_policy_id": dense_order[0],
                        "bm25_top_score": round(policy_bm25[bm25_order[0]], 6),
                        "dense_top_score": round(policy_dense[dense_order[0]], 6),
                        "bm25_unique_top": bm25_unique,
                        "dense_unique_top": dense_unique,
                    }
                    query_expansion_terms = _bilingual_query_matches(query)
                    if (bm25_unique and dense_unique and policy_bm25[bm25_order[0]] > 0
                            and policy_dense[dense_order[0]] > 0 and bm25_order[0] == dense_order[0]):
                        policy = next(row for row in candidates if row.policy_id == bm25_order[0])
                        bm25_clause_order = sorted(clause_ids, key=lambda key: (-bm25_clause_scores[key], key))
                        dense_clause_order = sorted(clause_ids, key=lambda key: (-dense_clause_scores[key], key))
                        fused = _rrf_rank([bm25_clause_order, dense_clause_order])
                        selected_id = next(key for key in fused if clause_by_id[key][0].policy_id == policy.policy_id)
                        selected_clause = clause_by_id[selected_id][1]
                        mode = "chroma_local_consensus"
                    else:
                        retrieval_ambiguous = True
                        mode = "registry_abstain"
                        retrieval_diagnostics["abstain_reason"] = "POLICY_RETRIEVAL_AMBIGUOUS"
                except Exception:
                    retrieval_ambiguous = True
                    embedding_error = True
                    mode = "registry_abstain"
                    retrieval_diagnostics["abstain_reason"] = "LOCAL_EMBEDDING_UNAVAILABLE"
        if policy is not None:
            if selected_clause is None:
                selected_clause = clauses_by_policy[policy.policy_id][0]
            evidence = [PolicyEvidence(
                policy_id=policy.policy_id, version=policy.version, title=policy.title,
                citation=str(selected_clause["text"]), source=f"{policy.policy_id}@{policy.version}",
                citation_ref=str(selected_clause["citation_ref"]), chunk_id=str(selected_clause["chunk_id"]),
                line_start=int(selected_clause["line_start"]), line_end=int(selected_clause["line_end"]), retrieval_mode=mode,
            )]
    event = _event(
        "retrieve_policy", "conflict" if policy_conflict else "low_relevance" if evidence_low_relevance else "completed" if policy else "scope_ambiguous" if scope_ambiguous else "scope_unmatched" if scope_not_found else "ambiguous" if retrieval_ambiguous else "no_policy",
        "Overlapping eligible policies contain conflicting structured rules; authorization is blocked." if policy_conflict else "No lexical evidence matched the active policy text; quote authorization is blocked." if evidence_low_relevance else "Retrieved evidence from the active, effective policy scope." if policy else "The inquiry describes multiple discount scopes; clarification is required." if scope_ambiguous else "No active policy matches this explicit scope." if scope_not_found else "Multiple eligible Registry policies remain; clarification is required." if retrieval_ambiguous else "No active effective policy applies; execution will fail closed.",
        {"policy_version": policy.version if policy else None, "retrieval_mode": mode, "candidate_count": len(candidates), "clause_count": sum(map(len, clauses_by_policy.values())) if candidates else 0, "citation_refs": [item.citation_ref for item in evidence], "query_expansion_terms": query_expansion_terms, "evidence_low_relevance": evidence_low_relevance, "policy_graph": policy_graph, "policy_conflict": policy_conflict, "scope": intent.policy_scope, "scope_ambiguous": scope_ambiguous, "scope_not_found": scope_not_found, "abstained": retrieval_ambiguous or policy_conflict or evidence_low_relevance, **retrieval_diagnostics},
    )
    return {
        "policy": policy, "evidence": [item.model_dump(mode="json") for item in evidence],
        "retrieval_ambiguous": retrieval_ambiguous,
        "evidence_low_relevance": evidence_low_relevance,
        "embedding_error": embedding_error,
        "policy_conflict": policy_conflict,
        "policy_conflict_edges": conflict_edges,
        "scope_ambiguous": scope_ambiguous,
        "scope_not_found": scope_not_found,
        "events": state.get("events", []) + [event],
    }


def _validate(state: DealGuardState) -> dict[str, Any]:
    intent = InquiryIntent.model_validate(state["intent"])
    if intent.items:
        customer, products, policy = state.get("customer"), state.get("products", []), state.get("policy")
        if state.get("policy_conflict"):
            decision = {"decision": "DENY", "reason_code": "POLICY_CONFLICT", "policy_id": None, "policy_version": None, "human_message": "Conflicting active policies apply to this inquiry, so no quote was created. Ask an administrator to resolve the policy conflict."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"], "conflicts": state.get("policy_conflict_edges", [])})]}
        if state.get("scope_ambiguous"):
            decision = {"decision": "NEEDS_INPUT", "reason_code": "POLICY_SCOPE_AMBIGUOUS", "policy_id": None, "policy_version": None, "human_message": "The inquiry mentions more than one discount program. Please clarify which policy scope to apply."}
            return {"decision": decision, "breakdown": None, "status": "needs_input", "events": state.get("events", []) + [_event("validate_proposal", "needs_input", decision["human_message"], {"reason_code": decision["reason_code"]})]}
        if state.get("scope_not_found"):
            decision = {"decision": "DENY", "reason_code": "POLICY_SCOPE_NOT_FOUND", "policy_id": None, "policy_version": None, "human_message": "No active policy is configured for the requested discount scope."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
        if state.get("evidence_low_relevance"):
            decision = {"decision": "DENY", "reason_code": "POLICY_EVIDENCE_LOW_RELEVANCE", "policy_id": None, "policy_version": None, "human_message": "No relevant policy text matched this inquiry, so the quote was not created. Please clarify the discount program or ask an administrator to review the policy."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
        if state.get("retrieval_ambiguous"):
            decision = {"decision": "DENY", "reason_code": "POLICY_RETRIEVAL_AMBIGUOUS", "policy_id": None, "policy_version": None, "human_message": "Applicable policy selection was ambiguous, so the quote was not created. Please ask an administrator to clarify the policy scope."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
        if customer is None or len(products) != len(intent.items):
            decision = {"decision": "DENY", "reason_code": "BUSINESS_DATA_NOT_FOUND", "policy_id": None, "policy_version": None, "human_message": "One or more requested products were not found in trusted business data."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"])]}
        if len({product.currency for product in products}) > 1:
            decision = {"decision": "DENY", "reason_code": "MIXED_CURRENCY_UNSUPPORTED", "policy_id": None, "policy_version": None, "human_message": "All items in one quote must use the same currency."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
        inventory_failure = aggregate_inventory_failure(
            (item.product_id, item.quantity, product.stock)
            for item, product in zip(intent.items, products, strict=True)
        )
        if inventory_failure:
            product_id, requested_quantity, available_stock = inventory_failure
            decision = {"decision": "DENY", "reason_code": "INSUFFICIENT_STOCK", "policy_id": policy.policy_id if policy else None,
                        "policy_version": policy.version if policy else None,
                        "human_message": f"Requested quantity {requested_quantity} for {product_id} exceeds the {available_stock} units currently available."}
            return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"], "product_id": product_id, "requested_quantity": requested_quantity, "available_stock": available_stock})]}
        line_results, decisions = [], []
        for item, product in zip(intent.items, products):
            proposal = ActionProposal(customer_id=customer.id, product_id=product.id, quantity=item.quantity, requested_discount_pct=item.requested_discount_pct)
            line_decision, breakdown, guard_checks = evaluate_quote_with_trace(state["actor"], customer, product, policy, proposal, intent.policy_scope)
            decisions.append(line_decision)
            evidence = next((item for item in state.get("evidence", []) if item["policy_id"] == policy.policy_id and item["version"] == policy.version), None) if policy else None
            line_results.append({"proposal": proposal.model_dump(mode="json"), "decision": line_decision.model_dump(mode="json"), "breakdown": breakdown.model_dump(mode="json") if breakdown else None, "product_name": product.name, "currency": product.currency, "unit_price": str(product.unit_price), "available_stock": product.stock, "inventory_checked_at": datetime.now(timezone.utc).isoformat(), "policy_source": policy.source if policy else "", "policy_provenance": quote_provenance(policy, line_decision, guard_checks, evidence.get("citation") if evidence else None, evidence.get("citation_ref") if evidence else None, evidence.get("chunk_id") if evidence else None) if policy else None})
        failed = next((d for d in decisions if d.decision == "DENY"), None)
        review = next((d for d in decisions if d.decision == "REVIEW"), None)
        if failed:
            chosen = failed
        else:
            chosen = decisions[0].model_copy(update={"decision": "REVIEW" if review else "ALLOW", "reason_code": review.reason_code if review else "WITHIN_POLICY", "human_message": "Every quote line passed policy checks." if not review else "At least one line requires manager approval."})
        first_proposal = line_results[0]["proposal"]
        proposal_data = {"customer_id": customer.id, "product_id": first_proposal["product_id"], "quantity": first_proposal["quantity"], "requested_discount_pct": first_proposal["requested_discount_pct"], "lines": [result["proposal"] for result in line_results]}
        event = _event("validate_proposal", chosen.decision.lower(), chosen.human_message, {"reason_code": chosen.reason_code, "line_count": len(line_results), "line_decisions": [d.model_dump(mode="json") for d in decisions], "line_provenance": [{"product_id": result["proposal"]["product_id"], "citation_ref": (result.get("policy_provenance") or {}).get("citation_ref"), "policy_source_hash": (result.get("policy_provenance") or {}).get("policy_source_hash"), "guard_evaluation": (result.get("policy_provenance") or {}).get("guard_evaluation")} for result in line_results]})
        return {"proposal": proposal_data, "line_results": line_results, "decision": chosen.model_dump(mode="json"), "breakdown": None, "status": "completed", "events": state.get("events", []) + [event]}
    missing = [name for name in ("product_id", "quantity", "requested_discount_pct") if getattr(intent, name) is None]
    if missing:
        decision = {
            "decision": "NEEDS_INPUT", "reason_code": "MISSING_REQUIRED_FIELDS",
            "policy_id": None, "policy_version": None,
            "human_message": "Please include product, quantity, and requested discount (for example: Product A, 1000 units, 8%). Missing: " + ", ".join(missing),
        }
        return {"decision": decision, "breakdown": None, "status": "needs_input", "events": state.get("events", []) + [_event("validate_proposal", "needs_input", decision["human_message"], {"missing": missing})]}
    if state.get("scope_ambiguous"):
        decision = {"decision": "NEEDS_INPUT", "reason_code": "POLICY_SCOPE_AMBIGUOUS", "policy_id": None, "policy_version": None, "human_message": "The inquiry mentions more than one discount program. Please clarify which policy scope to apply."}
        return {"decision": decision, "breakdown": None, "status": "needs_input", "events": state.get("events", []) + [_event("validate_proposal", "needs_input", decision["human_message"], {"reason_code": decision["reason_code"]})]}
    if state.get("policy_conflict"):
        decision = {"decision": "DENY", "reason_code": "POLICY_CONFLICT", "policy_id": None, "policy_version": None, "human_message": "Conflicting active policies apply to this inquiry, so no quote was created. Ask an administrator to resolve the policy conflict."}
        return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"], "conflicts": state.get("policy_conflict_edges", [])})]}
    if state.get("scope_not_found"):
        decision = {"decision": "DENY", "reason_code": "POLICY_SCOPE_NOT_FOUND", "policy_id": None, "policy_version": None, "human_message": "No active policy is configured for the requested discount scope."}
        return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
    if state.get("evidence_low_relevance"):
        decision = {"decision": "DENY", "reason_code": "POLICY_EVIDENCE_LOW_RELEVANCE", "policy_id": None, "policy_version": None, "human_message": "No relevant policy text matched this inquiry, so the quote was not created. Please clarify the discount program or ask an administrator to review the policy."}
        return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
    if state.get("retrieval_ambiguous"):
        decision = {"decision": "DENY", "reason_code": "POLICY_RETRIEVAL_AMBIGUOUS", "policy_id": None, "policy_version": None, "human_message": "Applicable policy selection was ambiguous, so the quote was not created. Please ask an administrator to clarify the policy scope."}
        return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"], {"reason_code": decision["reason_code"]})]}
    customer, product, policy = state.get("customer"), state.get("product"), state.get("policy")
    if customer is None or product is None:
        decision = {"decision": "DENY", "reason_code": "BUSINESS_DATA_NOT_FOUND", "policy_id": None, "policy_version": None, "human_message": "The requested customer or product was not found in trusted business data."}
        return {"decision": decision, "breakdown": None, "status": "completed", "events": state.get("events", []) + [_event("validate_proposal", "denied", decision["human_message"])]}
    proposal = ActionProposal(
        customer_id=customer.id, product_id=product.id, quantity=intent.quantity,
        requested_discount_pct=intent.requested_discount_pct,
    )
    decision, breakdown, guard_checks = evaluate_quote_with_trace(state["actor"], customer, product, policy, proposal, intent.policy_scope)
    evidence = next((item for item in state.get("evidence", []) if policy and item["policy_id"] == policy.policy_id and item["version"] == policy.version), None)
    line_results = [{"proposal": proposal.model_dump(mode="json"), "decision": decision.model_dump(mode="json"), "breakdown": breakdown.model_dump(mode="json") if breakdown else None, "product_name": product.name, "currency": product.currency, "unit_price": str(product.unit_price), "available_stock": product.stock, "inventory_checked_at": datetime.now(timezone.utc).isoformat(), "policy_source": policy.source if policy else "", "policy_provenance": quote_provenance(policy, decision, guard_checks, evidence.get("citation") if evidence else None, evidence.get("citation_ref") if evidence else None, evidence.get("chunk_id") if evidence else None) if policy else None}]
    return {
        "proposal": proposal.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "breakdown": breakdown.model_dump(mode="json") if breakdown else None,
        "line_results": line_results,
        "status": "completed",
        "events": state.get("events", []) + [_event("validate_proposal", decision.decision.lower(), decision.human_message, {"reason_code": decision.reason_code, "policy_version": decision.policy_version, "line_provenance": [{"product_id": product.id, "citation_ref": (line_results[0].get("policy_provenance") or {}).get("citation_ref"), "policy_source_hash": (line_results[0].get("policy_provenance") or {}).get("policy_source_hash"), "guard_evaluation": (line_results[0].get("policy_provenance") or {}).get("guard_evaluation")} ]})],
    }


def _save_quote(state: DealGuardState) -> dict[str, Any]:
    db = state["session"]
    policy = state["policy"]
    product = state["product"]
    breakdown = state["breakdown"]
    if state.get("line_results"):
        results = state["line_results"]
        lines = [{"product_id": r["proposal"]["product_id"], "product_name": r["product_name"], "currency": r["currency"], "quantity": r["proposal"]["quantity"], "unit_price": r["unit_price"], "discount_pct": r["proposal"]["requested_discount_pct"], "subtotal": r["breakdown"]["subtotal"], "discount_amount": r["breakdown"]["discount_amount"], "total": r["breakdown"]["total"], "policy_id": r["decision"]["policy_id"], "policy_version": r["decision"]["policy_version"], "policy_source": r["policy_source"], "policy_scope": state["intent"]["policy_scope"], "available_stock": r["available_stock"], "inventory_checked_at": r["inventory_checked_at"], **(r["policy_provenance"] or {})} for r in results]
        first = lines[0]
        subtotal = sum((Decimal(line["subtotal"]) for line in lines), Decimal("0"))
        discount_amount = sum((Decimal(line["discount_amount"]) for line in lines), Decimal("0"))
        total = sum((Decimal(line["total"]) for line in lines), Decimal("0"))
        row = QuoteDraft(tenant_id=state["actor"].tenant_id, owner_actor_id=state["actor"].id, customer_id=state["proposal"]["customer_id"], product_key=state["products"][0].internal_id, product_id=first["product_id"], quantity=sum(line["quantity"] for line in lines), unit_price=Decimal(first["unit_price"]), discount_pct=Decimal(first["discount_pct"]), subtotal=subtotal, discount_amount=discount_amount, total=total, policy_id=first["policy_id"], policy_version=first["policy_version"], policy_scope=state["intent"]["policy_scope"], lines=lines, status="DRAFT")
        db.add(row); db.flush(); db.refresh(row)
        quote = {"id": row.id, "status": "DRAFT", "customer_id": row.customer_id, "product_id": row.product_id, "breakdown": {"currency": first["currency"], "unit_price": str(row.unit_price), "quantity": row.quantity, "discount_pct": str(row.discount_pct), "subtotal": str(row.subtotal), "discount_amount": str(row.discount_amount), "total": str(row.total)}, "policy_id": row.policy_id, "policy_version": row.policy_version, "policy_scope": row.policy_scope, "created_at": row.created_at.isoformat(), "lines": lines}
        return {"quote": quote, "events": state.get("events", []) + [_event("create_quote_draft", "completed", "Created a quote draft after every line passed Guard.", {"quote_id": row.id, "line_count": len(lines)})]}
    proposal = ActionProposal.model_validate(state["proposal"])
    line = {"product_id": product.id, "product_name": product.name, "currency": product.currency, "quantity": proposal.quantity, "unit_price": str(product.unit_price), "discount_pct": str(proposal.requested_discount_pct), "subtotal": str(breakdown.subtotal), "discount_amount": str(breakdown.discount_amount), "total": str(breakdown.total), "policy_id": policy.policy_id, "policy_version": policy.version, "policy_source": policy.source, "policy_scope": state["intent"]["policy_scope"], "available_stock": product.stock, "inventory_checked_at": datetime.now(timezone.utc).isoformat(), **(state.get("line_results", [{}])[0].get("policy_provenance") or {})}
    row = QuoteDraft(
        tenant_id=state["actor"].tenant_id,
        owner_actor_id=state["actor"].id, customer_id=proposal.customer_id,
        product_key=product.internal_id, product_id=product.sku, quantity=proposal.quantity,
        unit_price=product.unit_price, discount_pct=proposal.requested_discount_pct,
        subtotal=breakdown["subtotal"], discount_amount=breakdown["discount_amount"],
        total=breakdown["total"], policy_id=policy.policy_id,
        policy_version=policy.version, policy_scope=state["intent"]["policy_scope"], lines=[line], status="DRAFT",
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
        "policy_scope": row.policy_scope,
        "created_at": row.created_at.isoformat(),
        "lines": [line],
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
    from app.agents.action_planner import plan_read_tools
    graph.add_node("action_planner", timed("action_planner", plan_read_tools))
    graph.add_node("load_business_data", timed("load_business_data", _load_business_data))
    graph.add_node("retrieve_policy", timed("retrieve_policy", _retrieve_policy))
    graph.add_node("validate_proposal", timed("validate_proposal", _validate))
    graph.add_node("save_quote", timed("save_quote", _save_quote))
    graph.add_edge(START, "parse_intent")
    graph.add_edge("parse_intent", "action_planner")
    graph.add_edge("action_planner", "load_business_data")
    graph.add_edge("load_business_data", "retrieve_policy")
    graph.add_edge("retrieve_policy", "validate_proposal")
    graph.add_conditional_edges("validate_proposal", _route_after_validation)
    graph.add_edge("save_quote", END)
    return graph.compile()
