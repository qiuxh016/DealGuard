import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import pytest
from sqlalchemy import func, select

from app.db.models import ApprovalRequest, Policy, QuoteDraft, Tenant
from app.rag.evaluation import TOPIC_TERMS, evaluate_offline


DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "policy_retrieval_eval"
POLICIES = json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))["policies"]
CASES = [json.loads(line) for line in (DATA_DIR / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


class DeterministicTopicEmbeddings:
    """Lexical test adapter for retrieval plumbing; not a semantic model."""

    def __init__(self, **_kwargs):
        pass

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = re.sub(r"[-\s]+", " ", text.lower())
        return [float(any(term in normalized for term in terms)) for terms in TOPIC_TERMS.values()]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def multi_policy_corpus(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "embedding_model", "deterministic-test-adapter")
    monkeypatch.setattr(settings, "local_embedding_model_path", "")
    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", DeterministicTopicEmbeddings)
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as db:
        for row in db.scalars(select(Policy).where(Policy.tenant_id == "harborlight-demo")).all():
            row.status = "inactive"
        db.add(Tenant(id="retrieval-decoy-tenant", name="Retrieval decoy"))
        for item in POLICIES:
            db.add(Policy(
                policy_id=item["policy_id"], version=item["version"], title=item["title"],
                action_type=item["action_type"], status="active", customer_tier=item["customer_tier"],
                policy_scope=item.get("policy_scope", "general"), scope_product_id=item.get("scope_product_id"),
                max_discount_pct=item["max_discount_pct"], review_max_discount_pct=item["review_max_discount_pct"],
                exception_approval_allowed=item["exception_approval_allowed"],
                effective_from=now - timedelta(days=1), source=item["source"], tenant_id="harborlight-demo",
            ))
        db.add_all([
            Policy(policy_id="EVAL-REFERENCE", version="v1", title="Reference only", action_type="reference_only",
                   status="active", customer_tier="VIP", max_discount_pct=None, exception_approval_allowed=False,
                   effective_from=now - timedelta(days=1), source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="harborlight-demo"),
            Policy(policy_id="EVAL-EXPIRED", version="v1", title="Expired discount decoy", action_type="quote_discount",
                   status="active", customer_tier="VIP", max_discount_pct=100, exception_approval_allowed=False,
                   effective_from=now - timedelta(days=2), effective_to=now - timedelta(seconds=1),
                   source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="harborlight-demo"),
            Policy(policy_id="EVAL-OTHER-TENANT", version="v1", title="Cross tenant decoy", action_type="quote_discount",
                   status="active", customer_tier="VIP", max_discount_pct=100, exception_approval_allowed=False,
                   effective_from=now - timedelta(days=1), source="Bulk discount promotion shipping campaign wholesale terms.", tenant_id="retrieval-decoy-tenant"),
        ])
        db.commit()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_multi_policy_case_retrieves_exact_citation_and_guard_decision(multi_policy_corpus, client, case):
    scope_hint = {"EVAL-BULK": "bulk order", "EVAL-LOYALTY": "loyalty reward", "EVAL-EARLYPAY": "early payment", "EVAL-SEASON": "seasonal campaign", "EVAL-PRODUCT": "product-specific"}[case["topic"]]
    inquiry = f"P-100. {scope_hint}. {case['query']}"
    response = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": "sales_a", "Idempotency-Key": f"retrieval-eval-{case['case_id']}"},
        json={"inquiry": inquiry},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["intent"]["quantity"] == case["quantity"]
    assert result["intent"]["requested_discount_pct"] == str(case["requested_discount_pct"])
    assert result["decision"]["decision"] == case["expected_decision"]
    assert result["decision"]["reason_code"] == case["expected_reason_code"]
    assert result["decision"]["policy_id"] == case["expected_policy_id"]
    assert result["decision"]["policy_version"] == case["expected_version"]
    assert len(result["policy_evidence"]) == 1
    evidence = result["policy_evidence"][0]
    assert evidence["source"] == f"{case['expected_policy_id']}@{case['expected_version']}"
    with client.app.state.session_factory() as db:
        policy = db.scalar(select(Policy).where(
            Policy.tenant_id == "harborlight-demo",
            Policy.policy_id == case["expected_policy_id"],
            Policy.version == case["expected_version"],
        ))
        assert evidence["citation"] == policy.source
        assert evidence["source"] not in {"EVAL-REFERENCE@v1", "EVAL-EXPIRED@v1", "EVAL-OTHER-TENANT@v1"}
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == case["expected_quote_count"]
        assert db.scalar(select(func.count()).select_from(ApprovalRequest)) == case["expected_approval_count"]


def test_multi_policy_benchmark_is_fixed_and_split_without_overlap():
    assert len(CASES) == 50
    assert len({case["case_id"] for case in CASES}) == 50
    counts = Counter((case["topic"], case["split"]) for case in CASES)
    assert counts == Counter({
        (policy["policy_id"], split): amount
        for policy in POLICIES
        for split, amount in (("development", 7), ("holdout", 3))
    })
    assert not {case["query"] for case in CASES if case["split"] == "development"} & {
        case["query"] for case in CASES if case["split"] == "holdout"
    }
    valid_ids = {policy["policy_id"] for policy in POLICIES}
    assert all(set(case["candidate_policy_ids"]) == valid_ids for case in CASES)


def test_scope_filter_reduces_single_candidate_to_registry_without_embedding(multi_policy_corpus, client, monkeypatch):
    from app.core.config import settings
    from app.agents import workflow

    monkeypatch.setattr(settings, "local_embedding_model_path", "local-test-model")
    monkeypatch.setattr(workflow, "_LocalPolicyEmbeddings", lambda _path: pytest.fail("single scoped candidate should use Registry"))
    result = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": "sales_a", "Idempotency-Key": "scoped-registry-only"},
        json={"inquiry": "bulk wholesale P-100 100 units at 1% discount"},
    ).json()

    assert result["decision"]["decision"] == "ALLOW"
    assert result["intent"]["policy_scope"] == "bulk_order"
    assert result["quote"] is not None
    assert len(result["policy_evidence"]) == 1
    assert result["policy_evidence"][0]["policy_id"] == "EVAL-BULK"
    assert result["policy_evidence"][0]["retrieval_mode"] == "registry"


def test_offline_evaluator_reports_mock_metrics_and_split_metrics():
    development = [case for case in CASES if case["split"] == "development"]
    holdout = [case for case in CASES if case["split"] == "holdout"]
    for split in (development, holdout):
        result = evaluate_offline(split, POLICIES)
        assert result["offline_test_adapter_only"] is True
        assert result["real_model_evaluation"] is False
        assert set(result["methods"]) == {"bm25", "deterministic_dense_test_adapter", "rrf"}
        assert all(metrics["cases"] == len(split) for metrics in result["methods"].values())
