"""Replay the frozen retrieval cases through the real API and deterministic Guard."""

from __future__ import annotations

import argparse
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
from app.db.seed import seed_demo_data  # noqa: E402
from app.main import app  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_retrieval_eval"


def _load_dataset(name: str) -> tuple[list[dict], list[dict], str, str | None]:
    cases_path = DATA_DIR / ("cases_v2.jsonl" if name == "retrieval_v2" else "cases.jsonl")
    policies_path = DATA_DIR / "policies.json"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    policies = json.loads(policies_path.read_text(encoding="utf-8"))["policies"]
    digest = hashlib.sha256(cases_path.read_bytes() + policies_path.read_bytes()).hexdigest()
    expectations_hash = None
    if name == "retrieval_v2":
        expectations_path = DATA_DIR / "guard_expectations_v2.jsonl"
        overrides = {
            row["case_id"]: row
            for row in (json.loads(line) for line in expectations_path.read_text(encoding="utf-8").splitlines() if line.strip())
        }
        if not set(overrides).issubset({case["case_id"] for case in cases}):
            raise ValueError("Guard expectation overrides must reference known case IDs.")
        cases = [{**case, **overrides.get(case["case_id"], {})} for case in cases]
        expectations_hash = hashlib.sha256(expectations_path.read_bytes()).hexdigest()
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
                action_type=item["action_type"], status="active", customer_tier=item["customer_tier"],
                policy_scope=item.get("policy_scope", "general"), scope_product_id=item.get("scope_product_id"),
                max_discount_pct=Decimal(item["max_discount_pct"]) if item["max_discount_pct"] is not None else None,
                review_max_discount_pct=Decimal(item["review_max_discount_pct"]) if item["review_max_discount_pct"] is not None else None,
                exception_approval_allowed=item["exception_approval_allowed"],
                effective_from=now - timedelta(days=1), source=item["source"], tenant_id="harborlight-demo",
            ))
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
        ])
        db.commit()


def replay(dataset: str, model_path: str, split: str) -> dict:
    cases, policies, dataset_hash, expectations_hash = _load_dataset(dataset)
    if split != "all":
        cases = [case for case in cases if case["split"] == split]
    settings.app_env = "development"
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
                rows.append({
                    "case_id": case["case_id"], "split": case["split"],
                    "expected_policy_id": case["expected_policy_id"], "cited_policy_id": cited_policy,
                    "decision_policy_id": decision.get("policy_id"),
                    "expected_decision": case["expected_decision"], "actual_decision": decision.get("decision"),
                    "expected_reason_code": case["expected_reason_code"], "actual_reason_code": decision.get("reason_code"),
                    "expected_quote_count": case["expected_quote_count"], "quote_write_count": quote_delta,
                    "expected_approval_count": case["expected_approval_count"], "approval_write_count": approval_delta,
                    "citation_matches": cited_policy == case["expected_policy_id"],
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
                    ("citation_accuracy_at_1", "citation_matches"),
                    ("decision_accuracy", "decision_matches"),
                    ("reason_code_accuracy", "reason_matches"),
                    ("write_outcome_accuracy", "writes_match"),
                )
            },
            "unexpected_quote_writes": sum(row["quote_write_count"] > row["expected_quote_count"] for row in group),
            "unexpected_approval_writes": sum(row["approval_write_count"] > row["expected_approval_count"] for row in group),
            "wrong_policy_allow_cases": sum(row["wrong_policy_allow"] for row in group),
            "false_rejections": sum(row["false_rejection"] for row in group),
        }
    for row in rows:
        row["task_success"] = row["citation_matches"] and row["decision_matches"] and row["reason_matches"] and row["writes_match"]
    # Recompute after task_success is assigned.
    for split_name, group in ((name, [row for row in rows if row["split"] == name]) for name in split_metrics):
        split_metrics[split_name]["task_success"] = sum(row["task_success"] for row in group) / len(group)
    return {
        "dataset": dataset, "dataset_sha256": dataset_hash,
        "guard_expectations_sha256": expectations_hash, "split": split, "model": Path(model_path).name,
        "runtime": "FastAPI TestClient + isolated SQLite + local Qwen3 and BM25 + deterministic Guard",
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
    parser.add_argument("--dataset", choices=("retrieval_v1", "retrieval_v2"), default="retrieval_v2")
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
