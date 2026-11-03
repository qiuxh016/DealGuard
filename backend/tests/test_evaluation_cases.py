import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import ApprovalRequest, Policy, Product, QuoteDraft


CASE_FILE = Path(__file__).resolve().parents[2] / "data" / "eval_cases.jsonl"
CASES = [json.loads(line) for line in CASE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(CASES) == 50
assert len({case["case_id"] for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_deterministic_evaluation_case(client, case):
    policies = client.get("/api/v1/policies").json()
    if case["active_policy"] is None:
        with client.app.state.session_factory() as db:
            for policy in db.scalars(select(Policy)).all():
                policy.status = "inactive"
            db.commit()
    else:
        policy = next(row for row in policies if row["version"] == case["active_policy"])
        activated = client.post(f"/api/v1/policies/{policy['id']}/activate", headers={"X-Demo-Actor": "admin"})
        assert activated.status_code == 200

    overrides = case.get("overrides", {})
    with client.app.state.session_factory() as db:
        if "stock" in overrides:
            db.get(Product, "P-100").stock = overrides["stock"]
        if overrides.get("expire_active_policy"):
            active = db.scalar(select(Policy).where(Policy.status == "active"))
            active.effective_to = datetime.now(timezone.utc)
        db.commit()

    payload = {"inquiry": case["inquiry"]}
    if case["actor"] in {"admin", "manager"}:
        payload["customer_id"] = "C-001"
    response = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": case["actor"], "Idempotency-Key": f"eval-{case['case_id']}"},
        json=payload,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    expected = case["expected"]
    assert result["execution_mode"] == "demo"
    assert result["decision"]["decision"] == expected["decision"]
    assert result["decision"]["reason_code"] == expected["reason_code"]
    assert result["decision"]["policy_version"] == expected["policy_version"]
    assert bool(result["quote"]) == (expected["quote_count"] == 1)
    assert bool(result.get("approval_request")) == (expected["approval_count"] == 1)
    if expected["quote_total"] is not None:
        assert result["quote"]["breakdown"]["total"] == expected["quote_total"]
    evidence_version = expected["evidence_version"]
    if evidence_version is not None:
        assert result["policy_evidence"]
        assert all(item["version"] == evidence_version for item in result["policy_evidence"])
    else:
        assert result["policy_evidence"] == []

    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == expected["quote_count"]
        assert db.scalar(select(func.count()).select_from(ApprovalRequest)) == expected["approval_count"]
