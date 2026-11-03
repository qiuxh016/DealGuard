import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.models import Customer, Policy, Tenant


CASE_FILE = Path(__file__).resolve().parents[2] / "data" / "retrieval_eval_cases.jsonl"
CASES = [json.loads(line) for line in CASE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(CASES) == 9
assert len({case["case_id"] for case in CASES}) == len(CASES)


TOPIC_TERMS = (
    ("discount", "rebate", "concession", "promotion", "reduction"),
    ("return", "refund", "reimbursement", "defective", "damaged", "replacement", "faulty"),
    ("freight", "delivery", "shipment", "transport", "shipping", "export"),
)
POLICY_DOCUMENTS = {
    "DISCOUNT-TEST": "Discount and rebate price concession promotion reduction terms.",
    "RETURNS-TEST": "Return refund reimbursement for defective damaged faulty goods and replacement terms.",
    "SHIPPING-TEST": "Freight delivery shipment transport shipping and export fee terms.",
}


class DeterministicTopicEmbeddings:
    """A test adapter for checking retrieval plumbing, not semantic model quality."""

    def __init__(self, **_kwargs):
        pass

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.lower()
        return [0.5 * float(any(term in normalized for term in terms)) for terms in TOPIC_TERMS]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _policy(policy_id: str, title: str, source: str, *, tenant_id: str = "harborlight-demo",
            status: str = "active", effective_to: datetime | None = None,
            customer_tier: str = "VIP") -> Policy:
    return Policy(
        policy_id=policy_id, version="v1", title=title, source=source,
        status=status, customer_tier=customer_tier, max_discount_pct=10,
        exception_approval_allowed=False, effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        effective_to=effective_to, tenant_id=tenant_id,
    )


@pytest.fixture
def retrieval_corpus(client):
    from app.agents import workflow
    now = datetime.now(timezone.utc)
    tenant_id = "retrieval-eval-tenant"
    with client.app.state.session_factory() as db:
        db.add_all([
            Tenant(id=tenant_id, name="Retrieval evaluation tenant"),
            Tenant(id="other-tenant", name="Other tenant"),
        ])
        db.flush()
        db.add(Customer(id="C-RETRIEVAL", name="Retrieval Evaluation Customer", tier="VIP", tenant_id=tenant_id))
        db.flush()
        db.add_all([
            _policy("DISCOUNT-TEST", "Discount terms", POLICY_DOCUMENTS["DISCOUNT-TEST"], tenant_id=tenant_id),
            _policy("RETURNS-TEST", "Returns and refunds", POLICY_DOCUMENTS["RETURNS-TEST"], tenant_id=tenant_id),
            _policy("SHIPPING-TEST", "Shipping charges", POLICY_DOCUMENTS["SHIPPING-TEST"], tenant_id=tenant_id),
            _policy("INACTIVE-TEST", "Inactive returns policy", "Return refund reimbursement defective goods terms.", tenant_id=tenant_id, status="inactive"),
            _policy("EXPIRED-TEST", "Expired freight policy", "Freight delivery shipment transport terms.", tenant_id=tenant_id, effective_to=now - timedelta(seconds=1)),
            _policy("OTHER-TENANT-TEST", "Other tenant discount policy", "Discount rebate price concession terms.", tenant_id="other-tenant"),
            _policy("STANDARD-TEST", "Standard tier returns policy", "Return refund damaged goods terms.", customer_tier="Standard"),
        ])
        db.commit()
        customer = db.get(Customer, "C-RETRIEVAL")
        yield workflow, db, customer


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_retrieval_case_returns_expected_stored_citation(retrieval_corpus, case):
    workflow, db, customer = retrieval_corpus
    db.query(Policy).filter_by(tenant_id=customer.tenant_id).update({Policy.status: "inactive"})
    db.query(Policy).filter_by(
        tenant_id=customer.tenant_id, policy_id=case["expected_policy_id"], version=case["expected_version"],
    ).update({Policy.status: "active"})
    db.commit()
    result = workflow._retrieve_policy({
        "session": db,
        "customer": customer,
        "raw_inquiry": case["query"],
        "events": [],
    })

    assert len(result["evidence"]) == 1
    evidence = result["evidence"][0]
    assert evidence["source"] == f'{case["expected_policy_id"]}@{case["expected_version"]}'
    assert evidence["version"] == case["expected_version"]
    assert evidence["retrieval_mode"] == "registry"
    stored_policy = db.query(Policy).filter_by(
        tenant_id=customer.tenant_id,
        policy_id=case["expected_policy_id"],
        version=case["expected_version"],
    ).one()
    assert evidence["citation"] in stored_policy.source
    assert evidence["citation_ref"].startswith(f"{case['expected_policy_id']}@{case['expected_version']}#L")


def test_retrieval_benchmark_cases_are_unique_and_have_required_labels():
    required = {"case_id", "query", "expected_policy_id", "expected_version"}
    assert all(required.issubset(case) for case in CASES)
    assert all(case["query"].strip() for case in CASES)


def test_retrieval_fails_closed_when_no_current_policy_applies(retrieval_corpus):
    workflow, db, customer = retrieval_corpus
    db.query(Policy).filter_by(tenant_id=customer.tenant_id).update({Policy.status: "inactive"})
    db.commit()

    result = workflow._retrieve_policy({
        "session": db,
        "customer": customer,
        "raw_inquiry": "Can we offer a customer a rebate?",
        "events": [],
    })

    assert result["policy"] is None
    assert result["evidence"] == []
    assert result["events"][-1]["status"] == "no_policy"


def test_retrieval_fails_closed_when_bm25_finds_no_evidence(retrieval_corpus, monkeypatch):
    workflow, db, customer = retrieval_corpus
    db.query(Policy).filter_by(tenant_id=customer.tenant_id).update({Policy.status: "inactive"})
    db.query(Policy).filter_by(tenant_id=customer.tenant_id, policy_id="DISCOUNT-TEST").update({Policy.status: "active"})
    db.commit()
    monkeypatch.setattr("app.rag.evaluation._bm25_scores", lambda _query, ids, _sources, **_kwargs: {item: 0 for item in ids})

    result = workflow._retrieve_policy({
        "session": db,
        "customer": customer,
        "raw_inquiry": "Can we offer a customer a rebate?",
        "events": [],
    })

    assert result["policy"] is None
    assert result["evidence"] == []
    assert result["evidence_low_relevance"] is True
    assert result["events"][-1]["status"] == "low_relevance"


def test_hybrid_retrieval_selects_policy_only_when_dense_and_bm25_agree(retrieval_corpus, monkeypatch):
    workflow, db, customer = retrieval_corpus
    monkeypatch.setattr(settings, "local_embedding_model_path", "test-model")

    class TestEmbeddings(DeterministicTopicEmbeddings):
        def __init__(self, _path):
            pass

    monkeypatch.setattr("app.rag.embeddings.LocalPolicyEmbeddings", TestEmbeddings)

    result = workflow._retrieve_policy({
        "session": db,
        "customer": customer,
        "raw_inquiry": "Can we provide a discount rebate?",
        "events": [],
    })

    assert result["policy"].policy_id == "DISCOUNT-TEST"
    assert result["evidence"][0]["retrieval_mode"] == "chroma_local_consensus"
    assert result["evidence"][0]["citation"] in result["policy"].source
    details = result["events"][-1]["details"]
    assert details["bm25_top_policy_id"] == details["dense_top_policy_id"] == "DISCOUNT-TEST"


def test_hybrid_retrieval_abstains_when_dense_and_bm25_disagree(retrieval_corpus, monkeypatch):
    workflow, db, customer = retrieval_corpus
    monkeypatch.setattr(settings, "local_embedding_model_path", "test-model")

    class DisagreeingEmbeddings:
        def __init__(self, _path):
            pass

        def embed_documents(self, texts):
            return [[0.1] if "Discount" in text else [1.0] for text in texts]

        def embed_query(self, _query):
            return [1.0]

    monkeypatch.setattr("app.rag.embeddings.LocalPolicyEmbeddings", DisagreeingEmbeddings)
    result = workflow._retrieve_policy({
        "session": db,
        "customer": customer,
        "raw_inquiry": "Can we provide a discount rebate?",
        "events": [],
    })

    assert result["policy"] is None
    assert result["evidence"] == []
    assert result["retrieval_ambiguous"] is True
    assert result["events"][-1]["details"]["abstain_reason"] == "POLICY_RETRIEVAL_AMBIGUOUS"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _bm25_rank(query: str) -> list[str]:
    documents = {policy_id: _tokens(source) for policy_id, source in POLICY_DOCUMENTS.items()}
    query_terms = set(_tokens(query))
    average_length = sum(map(len, documents.values())) / len(documents)
    scores: dict[str, float] = {}
    for policy_id, document in documents.items():
        score = 0.0
        for term in query_terms:
            frequency = document.count(term)
            if frequency == 0:
                continue
            document_frequency = sum(term in other for other in documents.values())
            inverse_frequency = math.log(1 + (len(documents) - document_frequency + 0.5) / (document_frequency + 0.5))
            score += inverse_frequency * frequency * 2.5 / (
                frequency + 1.5 * (1 - 0.75 + 0.75 * len(document) / average_length)
            )
        scores[policy_id] = score
    return sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))


def _dense_rank(query: str) -> list[str]:
    query_vector = DeterministicTopicEmbeddings._vector(query)
    scores = {}
    for policy_id, source in POLICY_DOCUMENTS.items():
        document_vector = DeterministicTopicEmbeddings._vector(source)
        scores[policy_id] = sum(left * right for left, right in zip(query_vector, document_vector))
    return sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))


def _rrf_rank(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, policy_id in enumerate(ranking, start=1):
            scores[policy_id] = scores.get(policy_id, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))


def _retrieval_metrics(rankings: list[list[list[str]]]) -> dict[str, float]:
    reciprocal_ranks = [1 / next((rank for rank, item in enumerate(ranking, start=1) if item == expected), 10**6)
                        for ranking, expected in zip(rankings, (case["expected_policy_id"] for case in CASES))]
    return {
        "hit_at_1": sum(rank == 1 for rank in reciprocal_ranks) / len(CASES),
        "mrr": sum(reciprocal_ranks) / len(CASES),
    }


def test_offline_bm25_dense_and_rrf_comparison():
    bm25_rankings = [_bm25_rank(case["query"]) for case in CASES]
    dense_rankings = [_dense_rank(case["query"]) for case in CASES]
    fused_rankings = [
        _rrf_rank([bm25, dense]) for bm25, dense in zip(bm25_rankings, dense_rankings)
    ]
    metrics = {
        "BM25": _retrieval_metrics(bm25_rankings),
        "deterministic_dense_test_adapter": _retrieval_metrics(dense_rankings),
        "RRF": _retrieval_metrics(fused_rankings),
    }
    print(json.dumps({"cases": len(CASES), "offline_mock_only": True, "metrics": metrics}, sort_keys=True))

    expected_metrics = {"hit_at_1": 1.0, "mrr": 1.0}
    assert metrics == {name: expected_metrics for name in ("BM25", "deterministic_dense_test_adapter", "RRF")}
