import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
def retrieval_corpus(client, monkeypatch):
    from app.agents import workflow
    from app.core.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_model", "deterministic-test-adapter")
    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", DeterministicTopicEmbeddings)
    now = datetime.now(timezone.utc)
    tenant_id = "retrieval-eval-tenant"
    with client.app.state.session_factory() as db:
        db.add_all([
            Tenant(id=tenant_id, name="Retrieval evaluation tenant"),
            Tenant(id="other-tenant", name="Other tenant"),
            Customer(id="C-RETRIEVAL", name="Retrieval Evaluation Customer", tier="VIP", tenant_id=tenant_id),
        ])
        db.add_all([
            _policy("DISCOUNT-TEST", "Discount terms", "Discount and rebate price concession promotion reduction terms.", tenant_id=tenant_id),
            _policy("RETURNS-TEST", "Returns and refunds", "Return refund reimbursement for defective damaged faulty goods and replacement terms.", tenant_id=tenant_id),
            _policy("SHIPPING-TEST", "Shipping charges", "Freight delivery shipment transport shipping and export fee terms.", tenant_id=tenant_id),
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
    assert evidence["retrieval_mode"] == "chroma"
    stored_policy = db.query(Policy).filter_by(
        tenant_id=customer.tenant_id,
        policy_id=case["expected_policy_id"],
        version=case["expected_version"],
    ).one()
    assert evidence["citation"] == stored_policy.source


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


def test_retrieval_fails_closed_when_index_returns_no_hit(retrieval_corpus, monkeypatch):
    workflow, db, customer = retrieval_corpus
    monkeypatch.setattr("langchain_chroma.Chroma.similarity_search_with_relevance_scores", lambda *_args, **_kwargs: [])

    result = workflow._retrieve_policy({
        "session": db,
        "customer": customer,
        "raw_inquiry": "Can we offer a customer a rebate?",
        "events": [],
    })

    assert result["policy"] is None
    assert result["evidence"] == []
    assert result["events"][-1]["status"] == "no_policy"
