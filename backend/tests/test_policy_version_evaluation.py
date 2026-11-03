import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import ApprovalRequest, Policy, QuoteDraft


CASE_FILE = Path(__file__).resolve().parents[2] / "data" / "policy_version_eval_cases.jsonl"
CASES = [json.loads(line) for line in CASE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_policy_version_case_uses_active_source_and_guard(client, case):
    policy = next(row for row in client.get("/api/v1/policies").json() if row["version"] == case["active_version"])
    activation = client.post(
        f"/api/v1/policies/{policy['id']}/activate",
        headers={"X-Demo-Actor": "admin"},
    )
    assert activation.status_code == 200, activation.text

    response = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": "sales_a", "Idempotency-Key": f"policy-eval-{case['case_id']}"},
        json={"inquiry": case["inquiry"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["decision"]["decision"] == case["expected_decision"]
    assert result["decision"]["reason_code"] == case["expected_reason_code"]
    assert result["decision"]["policy_id"] == "DISCOUNT-01"
    assert result["decision"]["policy_version"] == case["active_version"]
    assert len(result["policy_evidence"]) == 1
    evidence = result["policy_evidence"][0]
    assert evidence["source"] == f"DISCOUNT-01@{case['active_version']}"
    assert evidence["version"] == case["active_version"]

    stored = client.get("/api/v1/policies").json()
    source = next(row["source"] for row in stored if row["version"] == case["active_version"])
    assert evidence["citation"] in source
    assert evidence["citation_ref"].startswith(f"DISCOUNT-01@{case['active_version']}#L")
    assert evidence["line_start"] >= 1 and evidence["line_end"] >= evidence["line_start"]

    with client.app.state.session_factory() as db:
        quote_count = db.scalar(select(func.count()).select_from(QuoteDraft))
        approval_count = db.scalar(select(func.count()).select_from(ApprovalRequest))
    if case["expected_decision"] == "ALLOW":
        assert result["quote"]["status"] == "DRAFT"
        assert quote_count == 1
        assert approval_count == 0
    elif case["expected_decision"] == "REVIEW":
        assert result["quote"] is None
        assert result["approval_request"] is not None
        assert quote_count == 0
        assert approval_count == 1
    else:
        assert result["quote"] is None
        assert result["approval_request"] is None
        assert quote_count == 0
        assert approval_count == 0


def test_policy_version_case_splits_are_balanced_and_disjoint():
    assert len(CASES) == 30
    assert len({case["case_id"] for case in CASES}) == len(CASES)
    split_counts = Counter((case["active_version"], case["split"]) for case in CASES)
    assert split_counts == Counter({(version, split): count
                                    for version in ("v1", "v2", "v3")
                                    for split, count in (("development", 7), ("holdout", 3))})
    assert not {case["inquiry"] for case in CASES if case["split"] == "development"} & {
        case["inquiry"] for case in CASES if case["split"] == "holdout"
    }


def test_multiple_current_policies_without_retrieval_provider_fail_closed(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "embedding_model", "")
    with client.app.state.session_factory() as db:
        db.add(Policy(
            policy_id="DISCOUNT-OTHER", version="v1", title="Competing discount policy",
            status="active", customer_tier="VIP", max_discount_pct=100,
            exception_approval_allowed=False, effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            source="A competing active discount policy.", tenant_id="harborlight-demo",
        ))
        db.commit()

    response = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": "sales_a"},
        json={"inquiry": "Quote 100 units of Product A with a 5% discount."},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["decision"]["decision"] == "DENY"
    assert result["decision"]["reason_code"] == "NO_APPLICABLE_POLICY"
    assert result["policy_evidence"] == []
    assert result["quote"] is None
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == 0
