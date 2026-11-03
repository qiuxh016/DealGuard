"""Repeatable A/B/C/D DealGuard agent ablation on the frozen business set."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import statistics
import sys
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine, select, func
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.workflow import _parse_demo_inquiry  # noqa: E402
from app.core.config import Settings, settings  # noqa: E402
from app.db.database import Base, get_db  # noqa: E402
from app.db.models import ApprovalRequest, Policy, Product, QuoteDraft  # noqa: E402
from app.db.seed import seed_demo_data  # noqa: E402
from app.main import app  # noqa: E402

CASE_PATH = ROOT / "data" / "eval_cases.jsonl"
MODEL_PATH = Settings(_env_file=ROOT / ".env").local_embedding_model_path


class MatrixChat:
    """Deterministic provider adapter; real provider evaluation remains opt-in."""

    def __init__(self, **_kwargs):
        self.mode = "parse"

    def with_structured_output(self, *_args, **_kwargs):
        self.mode = "parse"
        return self

    def bind_tools(self, _tools):
        self.mode = "planner"
        return self

    def invoke(self, prompt):
        from langchain_core.messages import AIMessage

        if self.mode == "parse":
            inquiry = str(prompt).rsplit("\n\n", 1)[-1]
            return _parse_demo_inquiry(inquiry)
        payload = json.loads(prompt[-1].content)
        intent = payload["parsed_intent"]
        inquiry = payload["inquiry"]
        if not intent.get("items") and not intent.get("product_id") and "Kestrel Garden Sprayer" in inquiry:
            return AIMessage(content="", tool_calls=[{
                "name": "search_products", "args": {"query": "Kestrel Garden Sprayer"},
                "id": "eval-search-product", "type": "tool_call",
            }])
        return AIMessage(content="No additional read-only lookup is needed.")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))], 2)


def _run_variant(name: str, cases: list[dict], with_embedding: bool, live_llm: bool = False) -> dict:
    planner_enabled = name in {"B", "D"}
    embedding_enabled = with_embedding and name in {"C", "D"}
    settings.app_env = "development"
    settings.enable_demo_auth = True
    settings.action_planner_enabled = planner_enabled
    settings.local_embedding_model_path = MODEL_PATH if embedding_enabled else ""
    if live_llm:
        provider = Settings(_env_file=ROOT / ".env")
        if not provider.llm_api_key or not provider.llm_model:
            raise RuntimeError("Live mode requires LLM_API_KEY and LLM_MODEL in the repository .env.")
        settings.llm_api_key = provider.llm_api_key
        settings.llm_model = provider.llm_model
        settings.llm_base_url = provider.llm_base_url
    else:
        settings.llm_api_key = "matrix-adapter"
        settings.llm_model = "deterministic-evaluation-adapter"

    original_chat = None
    import langchain_openai

    original_chat = langchain_openai.ChatOpenAI
    if not live_llm:
        langchain_openai.ChatOpenAI = MatrixChat
    engine = create_engine(URL.create("sqlite", database=":memory:"), connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        seed_demo_data(db)
        db.add(Product(id="P-EVAL-KS", name="Kestrel Garden Sprayer", currency="USD", unit_price=Decimal("47.50"), stock=500, tenant_id="harborlight-demo"))
        db.commit()

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def isolated_lifespan(_app):
        yield

    app.router.lifespan_context = isolated_lifespan
    outcomes = []
    elapsed = []
    tool_counts = []
    quote_unauthorized_writes = 0
    try:
        with TestClient(app) as client:
            tool_challenge = {
                "case_id": "tool-product-name-resolution", "split": "agent_capability",
                "actor": "sales_a", "active_policy": "v1",
                "inquiry": "Please quote Kestrel Garden Sprayer, 100 units, at a 5% discount.",
                "overrides": {},
            }
            for case in [*cases, tool_challenge]:
                with factory() as db:
                    policies = db.scalars(select(Policy).where(Policy.tenant_id == "harborlight-demo", Policy.policy_id == "DISCOUNT-01")).all()
                    for policy in policies:
                        policy.status = "active" if policy.version == case["active_policy"] else "inactive"
                        policy.effective_to = None
                    product = db.get(Product, "P-100")
                    product.stock = 2000
                    overrides = case.get("overrides", {})
                    if "stock" in overrides:
                        product.stock = overrides["stock"]
                    if overrides.get("expire_active_policy"):
                        active = next((policy for policy in policies if policy.status == "active"), None)
                        if active:
                            active.effective_to = datetime.now(timezone.utc)
                    db.commit()
                    quotes_before = db.scalar(select(func.count()).select_from(QuoteDraft))
                    approvals_before = db.scalar(select(func.count()).select_from(ApprovalRequest))

                payload = {"inquiry": case["inquiry"]}
                if case["actor"] in {"admin", "manager"}:
                    payload["customer_id"] = "C-001"
                started = perf_counter()
                response = client.post(
                    "/api/v1/runs",
                    headers={"X-Demo-Actor": case["actor"], "Idempotency-Key": f"matrix-{name}-{case['case_id']}"},
                    json=payload,
                )
                latency = (perf_counter() - started) * 1000
                if response.status_code != 200:
                    raise RuntimeError(f"Variant {name}, case {case['case_id']} returned {response.status_code}.")
                result = response.json()
                is_tool_challenge = case["case_id"] == tool_challenge["case_id"]
                if is_tool_challenge and planner_enabled:
                    expected = {"decision": "ALLOW", "reason_code": "WITHIN_POLICY", "policy_version": "v1", "quote_count": 1, "approval_count": 0, "quote_total": "4512.50", "evidence_version": "v1"}
                elif is_tool_challenge:
                    expected = {"decision": "NEEDS_INPUT", "reason_code": "MISSING_REQUIRED_FIELDS", "policy_version": None, "quote_count": 0, "approval_count": 0, "quote_total": None, "evidence_version": None}
                else:
                    expected = case["expected"]
                with factory() as db:
                    quote_delta = db.scalar(select(func.count()).select_from(QuoteDraft)) - quotes_before
                    approval_delta = db.scalar(select(func.count()).select_from(ApprovalRequest)) - approvals_before
                quote_total = (result.get("quote") or {}).get("breakdown", {}).get("total")
                trace = result.get("trace", [])
                planner = next((event for event in trace if event.get("step") == "action_planner"), {})
                calls = planner.get("details", {}).get("tool_calls", [])
                tool_counts.append(len(calls))
                elapsed.append(latency)
                matches = (
                    result.get("decision", {}).get("decision") == expected["decision"]
                    and result.get("decision", {}).get("reason_code") == expected["reason_code"]
                    and result.get("decision", {}).get("policy_version") == expected["policy_version"]
                    and quote_delta == expected["quote_count"]
                    and approval_delta == expected["approval_count"]
                    and quote_total == expected["quote_total"]
                    and all(item.get("version") == expected["evidence_version"] for item in result.get("policy_evidence", []))
                )
                outcomes.append({
                    "case_id": case["case_id"], "success": matches,
                    "evaluation_group": "tool_capability" if is_tool_challenge else "business_regression",
                    "expected_decision": expected["decision"], "actual_decision": result.get("decision", {}).get("decision"),
                    "expected_reason_code": expected["reason_code"], "actual_reason_code": result.get("decision", {}).get("reason_code"),
                    "actual_intent": result.get("intent"),
                    "input_tokens": result.get("input_tokens"),
                    "output_tokens": result.get("output_tokens"),
                    "quote_write_count": quote_delta, "approval_write_count": approval_delta,
                    "expected_quote_write_count": expected["quote_count"], "latency_ms": round(latency, 2),
                    "tool_calls": calls,
                })
                if quote_delta > expected["quote_count"] or approval_delta > expected["approval_count"]:
                    quote_unauthorized_writes += 1
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if not live_llm:
            langchain_openai.ChatOpenAI = original_chat

    core_outcomes = [item for item in outcomes if item["evaluation_group"] == "business_regression"]
    tool_outcomes = [item for item in outcomes if item["evaluation_group"] == "tool_capability"]
    successful = sum(bool(item["success"]) for item in core_outcomes)
    tool_success = sum(bool(item["success"]) for item in tool_outcomes)
    selected_tool = tool_outcomes[0]["tool_calls"] if tool_outcomes else []
    return {
        "variant": name,
        "configuration": {"dynamic_tool_planning": planner_enabled, "local_qwen3_hybrid_retrieval": embedding_enabled, "guard_enabled": True},
        "provider_mode": "configured_openai_compatible_provider" if live_llm else "deterministic_evaluation_adapter",
        "cases": len(core_outcomes),
        "task_success_count": successful,
        "task_success_rate": round(successful / len(core_outcomes), 4) if core_outcomes else None,
        "tool_capability_case_success": bool(tool_success),
        "tool_selection_accuracy": (("search_products" in selected_tool) if live_llm else (selected_tool == ["search_products"])) if planner_enabled else None,
        "tool_capability_expected": "resolves an inquiry product name through a read-only search" if planner_enabled else "baseline asks for missing product ID",
        "unauthorized_write_cases": quote_unauthorized_writes,
        "mean_latency_ms": round(statistics.mean(elapsed), 2) if elapsed else None,
        "first_case_latency_ms": round(elapsed[0], 2) if elapsed else None,
        "p50_latency_ms": _percentile(elapsed, 0.50),
        "p95_latency_ms": _percentile(elapsed, 0.95),
        "mean_tool_calls": round(statistics.mean(tool_counts), 3) if tool_counts else 0,
        "input_tokens": sum(item["input_tokens"] or 0 for item in outcomes) if any(item["input_tokens"] is not None for item in outcomes) else None,
        "output_tokens": sum(item["output_tokens"] or 0 for item in outcomes) if any(item["output_tokens"] is not None for item in outcomes) else None,
        "tool_selection_counts": dict(Counter(tool for item in outcomes for tool in item["tool_calls"])),
        "failures": [item for item in outcomes if not item["success"]],
        "case_results": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed-workflow/tool-planning and BM25/Hybrid ablations.")
    parser.add_argument("--split", choices=("all", "policy_matrix", "edge_case", "identity_boundary"), default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "agent-evaluation-matrix.json")
    parser.add_argument("--no-local-embedding", action="store_true")
    parser.add_argument("--live-llm", action="store_true", help="Use the configured provider for parser and planner calls; may incur API usage.")
    args = parser.parse_args()
    if args.split == "all":
        cases = [json.loads(line) for line in CASE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        cases = [json.loads(line) for line in CASE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        cases = [case for case in cases if case["split"] == args.split]
    if not cases:
        parser.error("The selected split has no cases.")
    use_embedding = not args.no_local_embedding and bool(MODEL_PATH and Path(MODEL_PATH).is_dir())
    if not use_embedding and not args.no_local_embedding:
        print("Local Qwen3 weights unavailable; C/D will report the BM25-only path.", file=sys.stderr)
    dataset_hash = sha256(CASE_PATH.read_bytes()).hexdigest()
    selected_cases_hash = sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    reports = [_run_variant(name, cases, use_embedding, args.live_llm) for name in ("A", "B", "C", "D")]
    payload = {
        "dataset": str(CASE_PATH.relative_to(ROOT)), "dataset_sha256": dataset_hash,
        "selected_cases_sha256": selected_cases_hash,
        "split": args.split, "case_count": len(cases),
        "provider_mode": "configured_openai_compatible_provider" if args.live_llm else "deterministic_evaluation_adapter",
        "guard_disabled_control": "omitted: no unguarded variant can access writable business data",
        "variants": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "dataset_sha256": dataset_hash,
        "variants": [{key: report[key] for key in ("variant", "task_success_rate", "tool_capability_case_success", "tool_selection_accuracy", "unauthorized_write_cases", "mean_latency_ms", "first_case_latency_ms", "p95_latency_ms", "mean_tool_calls", "input_tokens", "output_tokens")} for report in reports],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
