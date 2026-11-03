"""Replay the frozen retrieval cases through the real API and deterministic Guard."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.db.database import Base, get_db  # noqa: E402
from app.db.models import ApprovalRequest, Policy, QuoteDraft, Tenant  # noqa: E402
from app.rag.policy_chunks import policy_clauses  # noqa: E402
from app.db.seed import seed_demo_data  # noqa: E402
from app.main import app  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_retrieval_eval"


def _load_dataset(name: str) -> tuple[list[dict], list[dict], str, str | None]:
    safety = name == "rag_safety"
    cases_path = DATA_DIR / ("cases_v2.jsonl" if name in {"retrieval_v2", "rag_safety"} else "cases.jsonl")
    policies_path = ROOT / "data" / "policy_rag_safety" / "policies.json" if safety else DATA_DIR / "policies.json"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    policies = json.loads(policies_path.read_text(encoding="utf-8"))["policies"]
    digest = hashlib.sha256(cases_path.read_bytes() + policies_path.read_bytes()).hexdigest()
    expectations_hash = None
    if name in {"retrieval_v2", "rag_safety"}:
        expectations_path = DATA_DIR / "guard_expectations_v2.jsonl"
        overrides = {
            row["case_id"]: row
            for row in (json.loads(line) for line in expectations_path.read_text(encoding="utf-8").splitlines() if line.strip())
        }
        if not set(overrides).issubset({case["case_id"] for case in cases}):
            raise ValueError("Guard expectation overrides must reference known case IDs.")
        cases = [{**case, **overrides.get(case["case_id"], {})} for case in cases]
        expectations_hash = hashlib.sha256(expectations_path.read_bytes()).hexdigest()
    if safety:
        boundary_path = ROOT / "data" / "policy_rag_safety" / "boundary_cases.jsonl"
        boundary_cases = [json.loads(line) for line in boundary_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        conflict = next(case for case in boundary_cases if case.get("scenario") == "overlap_conflict")
        cases.append({
            "case_id": conflict["case_id"], "split": "safety", "expected_policy_id": None,
            "expected_version": None, "quantity": 100, "requested_discount_pct": 1,
            "query": conflict["query"], "expected_decision": "DENY", "expected_reason_code": "POLICY_CONFLICT",
            "expected_quote_count": 0, "expected_approval_count": 0, "scenario": "overlap_conflict",
        })
        for boundary in boundary_cases:
            if boundary.get("scenario") not in {"multi_candidate", "multi_disagreement"}:
                continue
            allow = boundary["expected_policy_id"] is not None
            cases.append({
                "case_id": boundary["case_id"], "split": "safety",
                "expected_policy_id": boundary["expected_policy_id"],
                "expected_version": "v1" if allow else None,
                "quantity": 100, "requested_discount_pct": 1,
                "query": boundary["query"],
                "expected_decision": "ALLOW" if allow else "DENY",
                "expected_reason_code": "WITHIN_POLICY" if allow else "POLICY_RETRIEVAL_AMBIGUOUS",
                "expected_quote_count": 1 if allow else 0,
                "expected_approval_count": 0,
                "scenario": boundary["scenario"],
            })
        digest = hashlib.sha256(bytes.fromhex(digest) + boundary_path.read_bytes()).hexdigest()
    return cases, policies, digest, expectations_hash


def _seed_eval_policies(session_factory, policies: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        for row in db.scalars(select(Policy).where(Policy.tenant_id == "harborlight-demo")).all():
            row.status = "inactive"
        db.add(Tenant(id="retrieval-decoy-tenant", name="Retrieval decoy"))
        for item in policies:
            db.add(Policy(
                policy_id=item["policy_id"], version=item["version"], title=item["title"],
                action_type=item["action_type"], status=item.get("status", "active"), customer_tier=item["customer_tier"],
                policy_scope=item.get("policy_scope", "general"), scope_product_id=item.get("scope_product_id"),
                max_discount_pct=Decimal(item["max_discount_pct"]) if item["max_discount_pct"] is not None else None,
                review_max_discount_pct=Decimal(item["review_max_discount_pct"]) if item["review_max_discount_pct"] is not None else None,
                exception_approval_allowed=item["exception_approval_allowed"],
                effective_from=now - timedelta(days=1), source=item["source"], tenant_id="harborlight-demo",
            ))
        alternate = next(
            item for item in json.loads((ROOT / "data" / "policy_rag_safety" / "policies.json").read_text(encoding="utf-8"))["policies"]
            if item["policy_id"] == "EVAL-BULK-ALT"
        )
        db.add_all([
            Policy(policy_id="EVAL-REFERENCE", version="v1", title="Reference only", action_type="reference_only",
                   status="active", customer_tier="VIP", max_discount_pct=None, exception_approval_allowed=False,
                   effective_from=now - timedelta(days=1), source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="harborlight-demo"),
            Policy(policy_id="EVAL-EXPIRED", version="v1", title="Expired discount decoy", action_type="quote_discount",
                   status="active", customer_tier="VIP", max_discount_pct=Decimal("100"), exception_approval_allowed=False,
                   effective_from=now - timedelta(days=2), effective_to=now - timedelta(seconds=1),
                   source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="harborlight-demo"),
            Policy(policy_id="EVAL-OTHER-TENANT", version="v1", title="Cross tenant decoy", action_type="quote_discount",
                   status="active", customer_tier="VIP", max_discount_pct=Decimal("100"), exception_approval_allowed=False,
                   effective_from=now - timedelta(days=1), source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="retrieval-decoy-tenant"),
            Policy(policy_id="EVAL-WRONG-SCOPE", version="v1", title="Wrong scope decoy", action_type="quote_discount",
                   status="active", customer_tier="VIP", policy_scope="product_specific", scope_product_id="P-999", max_discount_pct=Decimal("100"), exception_approval_allowed=False,
                   effective_from=now - timedelta(days=1), source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="harborlight-demo"),
            Policy(policy_id="EVAL-BULK-CONFLICT", version="v1", title="Conflicting bulk limit", action_type="quote_discount",
                   status="inactive", customer_tier="VIP", policy_scope="bulk_order", max_discount_pct=Decimal("4"), exception_approval_allowed=False,
                   effective_from=now - timedelta(days=1), source="A conflicting structured direct discount limit is 4%.", tenant_id="harborlight-demo"),
            Policy(policy_id=alternate["policy_id"], version=alternate["version"], title=alternate["title"],
                   action_type=alternate["action_type"], status="inactive", customer_tier=alternate["customer_tier"],
                   policy_scope=alternate["policy_scope"], scope_product_id=alternate.get("scope_product_id"),
                   max_discount_pct=Decimal(alternate["max_discount_pct"]),
                   exception_approval_allowed=alternate["exception_approval_allowed"],
                   effective_from=now - timedelta(days=1), source=alternate["source"], tenant_id="harborlight-demo"),
        ])
        db.commit()


def _conditional_rate(rows: list[dict], metric: str, condition: str) -> float | None:
    expected = [row for row in rows if row[condition]]
    return sum(row[metric] for row in expected) / len(expected) if expected else None


def replay(dataset: str, model_path: str, split: str) -> dict:
    cases, policies, dataset_hash, expectations_hash = _load_dataset(dataset)
    if split != "all":
        cases = [case for case in cases if case["split"] == split]
    settings.app_env = "development"
    settings.enable_demo_auth = True
    settings.llm_model = ""
    settings.local_embedding_model_path = model_path

    with tempfile.TemporaryDirectory(prefix="dealguard-guard-eval-") as temp_dir:
        engine = create_engine(
            f"sqlite:///{Path(temp_dir) / 'replay.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        with session_factory() as db:
            seed_demo_data(db)
        _seed_eval_policies(session_factory, policies)

        def override_db():
            db = session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        test_client = TestClient(app)
        rows = []
        try:
            for case in cases:
                with session_factory() as db:
                    conflict_policy = db.scalar(select(Policy).where(Policy.tenant_id == "harborlight-demo", Policy.policy_id == "EVAL-BULK-CONFLICT"))
                    conflict_policy.status = "active" if case.get("scenario") == "overlap_conflict" else "inactive"
                    alternate = db.scalar(select(Policy).where(Policy.tenant_id == "harborlight-demo", Policy.policy_id == "EVAL-BULK-ALT"))
                    alternate.status = "active" if case.get("scenario") in {"multi_candidate", "multi_disagreement"} else "inactive"
                    db.commit()
                inquiry = f"P-100 {case['quantity']} units at {case['requested_discount_pct']}% discount. {case['query']}"
                with session_factory() as db:
                    quotes_before = db.scalar(select(func.count()).select_from(QuoteDraft))
                    approvals_before = db.scalar(select(func.count()).select_from(ApprovalRequest))
                response = test_client.post(
                    "/api/v1/runs",
                    headers={"X-Demo-Actor": "sales_a", "Idempotency-Key": f"guard-eval-{dataset}-{case['case_id']}"},
                    json={"inquiry": inquiry},
                )
                if response.status_code != 200:
                    raise RuntimeError(f"API replay failed for {case['case_id']}: {response.status_code}")
                result = response.json()
                with session_factory() as db:
                    quotes_after = db.scalar(select(func.count()).select_from(QuoteDraft))
                    approvals_after = db.scalar(select(func.count()).select_from(ApprovalRequest))
                quote_delta = quotes_after - quotes_before
                approval_delta = approvals_after - approvals_before
                evidence = result.get("policy_evidence") or []
                cited_policy = evidence[0]["policy_id"] if evidence else None
                decision = result.get("decision") or {}
                retrieval_event = next((event for event in result.get("trace", []) if event.get("step") == "retrieve_policy"), {})
                retrieval_mode = (retrieval_event.get("details") or {}).get("retrieval_mode")
                policy_by_id = {policy["policy_id"]: policy for policy in policies}
                stored_policy = policy_by_id.get(case["expected_policy_id"])
                evidence_text = evidence[0]["citation"] if evidence else None
                citation_is_exact_clause = False
                if evidence and stored_policy:
                    allowed_chunks = policy_clauses(stored_policy["policy_id"], stored_policy["version"], stored_policy["source"])
                    citation_is_exact_clause = evidence_text in [chunk["text"] for chunk in allowed_chunks]
                quote_line = (result.get("quote") or {}).get("lines", [])
                quote_line = quote_line[0] if quote_line else None
                approval_line = None
                approval_id = (result.get("approval_request") or {}).get("id")
                if approval_id:
                    with session_factory() as db:
                        approval_row = db.get(ApprovalRequest, approval_id)
                        if approval_row and approval_row.lines:
                            approval_line = approval_row.lines[0]
                provenance_line = quote_line or ((approval_line or {}).get("policy_provenance") if approval_line else None)
                citation_expected = case["expected_policy_id"] is not None
                rows.append({
                    "case_id": case["case_id"], "split": case["split"],
                    "expected_policy_id": case["expected_policy_id"], "cited_policy_id": cited_policy,
                    "decision_policy_id": decision.get("policy_id"),
                    "expected_decision": case["expected_decision"], "actual_decision": decision.get("decision"),
                    "expected_reason_code": case["expected_reason_code"], "actual_reason_code": decision.get("reason_code"),
                    "expected_quote_count": case["expected_quote_count"], "quote_write_count": quote_delta,
                    "expected_approval_count": case["expected_approval_count"], "approval_write_count": approval_delta,
                    "citation_matches": cited_policy == case["expected_policy_id"],
                    "retrieval_mode": retrieval_mode,
                    "citation_expected": citation_expected,
                    "citation_source_integrity": bool(evidence and stored_policy and evidence_text in stored_policy["source"]),
                    "citation_is_exact_clause": citation_is_exact_clause,
                    "citation_ref_matches_version": bool(evidence and evidence[0]["citation_ref"].startswith(f"{case['expected_policy_id']}@{case['expected_version']}#L")) if citation_expected else not evidence,
                    "line_provenance_expected": case["expected_decision"] in {"ALLOW", "REVIEW"},
                    "line_provenance_present": bool(provenance_line and provenance_line.get("policy_source_hash") and provenance_line.get("guard_evaluation")),
                    "line_provenance_hash_matches": bool(provenance_line and stored_policy and provenance_line.get("policy_source_hash") == hashlib.sha256(stored_policy["source"].encode("utf-8")).hexdigest()),
                    "line_provenance_citation_matches": bool(provenance_line and evidence and provenance_line.get("citation_ref") == evidence[0]["citation_ref"]),
                    "decision_matches": decision.get("decision") == case["expected_decision"],
                    "reason_matches": decision.get("reason_code") == case["expected_reason_code"],
                    "writes_match": quote_delta == case["expected_quote_count"] and approval_delta == case["expected_approval_count"],
                    "wrong_policy_allow": decision.get("decision") == "ALLOW" and cited_policy != case["expected_policy_id"],
                    "false_rejection": case["expected_decision"] in {"ALLOW", "REVIEW"} and decision.get("decision") == "DENY",
                })
        finally:
            app.dependency_overrides.clear()
            engine.dispose()

    split_metrics = {}
    for split_name in sorted({row["split"] for row in rows}):
        group = [row for row in rows if row["split"] == split_name]
        split_metrics[split_name] = {
            "cases": len(group),
            **{
                metric: sum(row[key] for row in group) / len(group) if group else 0.0
                for metric, key in (
                    ("decision_accuracy", "decision_matches"),
                    ("reason_code_accuracy", "reason_matches"),
                    ("write_outcome_accuracy", "writes_match"),
                )
            },
            "citation_accuracy_at_1": _conditional_rate(group, "citation_matches", "citation_expected"),
            "unexpected_quote_writes": sum(row["quote_write_count"] > row["expected_quote_count"] for row in group),
            "unexpected_approval_writes": sum(row["approval_write_count"] > row["expected_approval_count"] for row in group),
            "wrong_policy_allow_cases": sum(row["wrong_policy_allow"] for row in group),
            "false_rejections": sum(row["false_rejection"] for row in group),
            "citation_source_integrity": _conditional_rate(group, "citation_source_integrity", "citation_expected"),
            "citation_is_exact_clause": _conditional_rate(group, "citation_is_exact_clause", "citation_expected"),
            "citation_ref_matches_version": _conditional_rate(group, "citation_ref_matches_version", "citation_expected"),
            "line_provenance_coverage": _conditional_rate(group, "line_provenance_present", "line_provenance_expected"),
            "line_provenance_hash_integrity": _conditional_rate(group, "line_provenance_hash_matches", "line_provenance_expected"),
            "line_provenance_citation_consistency": _conditional_rate(group, "line_provenance_citation_matches", "line_provenance_expected"),
        }
    for row in rows:
        row["task_success"] = row["citation_matches"] and row["decision_matches"] and row["reason_matches"] and row["writes_match"]
    # Recompute after task_success is assigned.
    for split_name, group in ((name, [row for row in rows if row["split"] == name]) for name in split_metrics):
        split_metrics[split_name]["task_success"] = sum(row["task_success"] for row in group) / len(group)
    return {
        "dataset": dataset, "dataset_sha256": dataset_hash,
        "guard_expectations_sha256": expectations_hash, "split": split, "model": Path(model_path).name,
        "runtime": "FastAPI TestClient + isolated SQLite + Registry filtering + local Qwen3/BM25 consensus retrieval + deterministic Guard",
        "retrieval_modes": dict(Counter(row["retrieval_mode"] for row in rows)),
        "cases": len(rows), "metrics_by_split": split_metrics,
        "totals": {
            "unexpected_quote_writes": sum(row["quote_write_count"] > row["expected_quote_count"] for row in rows),
            "unexpected_approval_writes": sum(row["approval_write_count"] > row["expected_approval_count"] for row in rows),
            "wrong_policy_allow_cases": sum(row["wrong_policy_allow"] for row in rows),
            "false_rejections": sum(row["false_rejection"] for row in rows),
            "task_successes": sum(row["task_success"] for row in rows),
        },
        "cases_detail": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a frozen policy retrieval set through the API and Guard.")
    parser.add_argument("--dataset", choices=("retrieval_v1", "retrieval_v2", "rag_safety"), default="retrieval_v2")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--model-path", type=Path, default=Path(settings.local_embedding_model_path) if settings.local_embedding_model_path else None)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.model_path is None or not args.model_path.exists():
        parser.error("Set LOCAL_EMBEDDING_MODEL_PATH or pass --model-path to the local Qwen3 model directory.")
    report = replay(args.dataset, str(args.model_path), args.split)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
