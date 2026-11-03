from hashlib import sha256
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select

from app.db.models import ApprovalRequest, Customer, Policy, Product, QuoteDraft, RunRecord
from app.services import approvals as approval_services
from app.api.routes import runs as runs_route


def count_quotes(client):
    with client.app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(QuoteDraft))


def count_runs(client):
    with client.app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(RunRecord))


def count_approvals(client):
    with client.app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(ApprovalRequest))


def request_hash(customer_id, inquiry):
    return sha256(f"{customer_id}\0{inquiry}".encode("utf-8")).hexdigest()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "dealguard-api"}


def test_normal_quote_uses_decimal_and_creates_draft(client):
    response = client.post("/api/v1/runs", json={"inquiry": "I need 1,000 units of Product A at an 8% discount."})
    assert response.status_code == 200
    result = response.json()
    assert result["decision"]["decision"] == "ALLOW"
    assert result["decision"]["policy_version"] == "v1"
    assert result["quote"]["status"] == "DRAFT"
    assert result["quote"]["breakdown"]["total"] == "92000.00"
    assert result["execution_mode"] == "demo"
    assert result["policy_evidence"][0]["source"] == "DISCOUNT-01@v1"
    assert [event["step"] for event in result["trace"]][-1] == "create_quote_draft"


def test_multi_item_quote_has_per_line_policy_snapshots_and_aggregate(client):
    payload = {"inquiry": "Please quote P-100 10 units at 5%; P-200 4 units at 3%."}
    first = client.post("/api/v1/runs", headers={"Idempotency-Key": "multi-item-repeat"}, json=payload).json()
    replay = client.post("/api/v1/runs", headers={"Idempotency-Key": "multi-item-repeat"}, json=payload).json()
    assert replay["run_id"] == first["run_id"] and replay["quote"]["id"] == first["quote"]["id"]
    result = first
    assert result["decision"]["decision"] == "ALLOW", result
    assert result["quote"]["breakdown"]["total"] == "1920.00"
    lines = result["quote"]["lines"]
    assert [line["product_id"] for line in lines] == ["P-100", "P-200"]
    assert [line["total"] for line in lines] == ["950.00", "970.00"]
    assert all(line["policy_id"] == "DISCOUNT-01" and line["policy_version"] == "v1" and "10%" in line["policy_source"] for line in lines)
    assert [line["available_stock"] for line in lines] == [2000, 800]
    assert all(line["inventory_checked_at"] for line in lines)
    assert count_quotes(client) == 1


@pytest.mark.parametrize(("inquiry", "reason"), [
    ("Please quote P-100 10 units at 5%; P-200 900 units at 3%.", "INSUFFICIENT_STOCK"),
    ("Please quote P-100 10 units at 5%; P-200 4 units at 12%.", "DISCOUNT_LIMIT_EXCEEDED"),
    ("Please quote P-100 10 units at 5%; P-999 4 units at 3%.", "BUSINESS_DATA_NOT_FOUND"),
])
def test_multi_item_failure_does_not_create_partial_quote(client, inquiry, reason):
    result = client.post("/api/v1/runs", json={"inquiry": inquiry}).json()
    assert result["decision"]["decision"] == "DENY"
    assert result["decision"]["reason_code"] == reason
    if reason == "DISCOUNT_LIMIT_EXCEEDED":
        assert Decimal(result["decision"]["suggested_discount_pct"]) == Decimal("10")
        assert result["decision"]["suggested_product_id"] == "P-200"
    assert result["quote"] is None
    assert count_quotes(client) == 0


def test_multi_item_csv_exports_each_line_and_policy_source(client):
    import csv
    from io import StringIO

    result = client.post("/api/v1/runs", json={"inquiry": "Please quote P-100 10 units at 5%; P-200 4 units at 3%."}).json()
    exported = client.get(f"/api/v1/quotes/{result['quote']['id']}/export.csv")
    assert exported.status_code == 200
    rows = list(csv.DictReader(StringIO(exported.text)))
    assert len(rows) == 2
    assert {row["product_id"] for row in rows} == {"P-100", "P-200"}
    assert all(row["policy_id"] == "DISCOUNT-01" and row["policy_version"] == "v1" for row in rows)
    assert all("10%" in row["policy_source"] for row in rows)


def test_multi_item_review_revalidates_and_approves_all_lines(client):
    _activate(client, "v3")
    result = client.post("/api/v1/runs", json={"inquiry": "Please quote P-100 10 units at 7%; P-200 4 units at 3%."}).json()
    assert result["decision"]["decision"] == "REVIEW", result
    assert result["quote"] is None and count_quotes(client) == 0
    approval_id = result["approval_request"]["id"]
    approved = client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "manager"}).json()
    assert approved["approval"]["state"] == "APPROVED"
    assert approved["quote"]["breakdown"]["total"] == "1900.00"
    assert [line["total"] for line in approved["quote"]["lines"]] == ["930.00", "970.00"]
    assert count_quotes(client) == 1


def test_idempotency_key_replays_quote_without_duplicate_writes(client):
    headers = {"Idempotency-Key": "quote-request-1"}
    payload = {"inquiry": "I need 1,000 units of Product A at an 8% discount."}
    first = client.post("/api/v1/runs", headers=headers, json=payload)
    repeated = client.post("/api/v1/runs", headers=headers, json=payload)
    assert first.status_code == repeated.status_code == 200
    assert repeated.json()["run_id"] == first.json()["run_id"]
    assert repeated.json()["quote"]["id"] == first.json()["quote"]["id"]
    assert count_runs(client) == 1
    assert count_quotes(client) == 1
    assert count_approvals(client) == 0


def test_idempotency_key_cannot_be_reused_for_a_different_request(client):
    headers = {"Idempotency-Key": "quote-request-1"}
    first = client.post("/api/v1/runs", headers=headers, json={"inquiry": "Quote 100 units of Product A with 5% discount."})
    conflict = client.post("/api/v1/runs", headers=headers, json={"inquiry": "Quote 200 units of Product A with 5% discount."})
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert count_runs(client) == 1
    assert count_quotes(client) == 1


def test_idempotency_key_is_scoped_to_the_demo_actor(client):
    key = "same-client-key"
    payload = {"inquiry": "Quote 100 units of Product A with 5% discount."}
    sales_a = client.post("/api/v1/runs", headers={"Idempotency-Key": key}, json=payload)
    sales_b = client.post("/api/v1/runs", headers={"Idempotency-Key": key, "X-Demo-Actor": "sales_b"}, json=payload)
    assert sales_a.status_code == sales_b.status_code == 200
    assert sales_a.json()["run_id"] != sales_b.json()["run_id"]
    assert count_runs(client) == 2
    assert count_quotes(client) == 1


def test_idempotency_key_replays_denials_and_review_requests(client):
    denied_key = {"Idempotency-Key": "denied-request"}
    denied_payload = {"inquiry": "Quote 1,000 units of Product A with 8% discount."}
    _activate(client, "v2")
    first_deny = client.post("/api/v1/runs", headers=denied_key, json=denied_payload)
    replay_deny = client.post("/api/v1/runs", headers=denied_key, json=denied_payload)
    assert first_deny.json()["run_id"] == replay_deny.json()["run_id"]
    assert replay_deny.json()["decision"]["decision"] == "DENY"
    assert count_quotes(client) == 0

    _activate(client, "v3")
    review_key = {"Idempotency-Key": "review-request"}
    first_review = client.post("/api/v1/runs", headers=review_key, json=denied_payload)
    replay_review = client.post("/api/v1/runs", headers=review_key, json=denied_payload)
    assert first_review.status_code == replay_review.status_code == 200
    assert first_review.json()["run_id"] == replay_review.json()["run_id"]
    assert first_review.json()["approval_request"]["id"] == replay_review.json()["approval_request"]["id"]
    assert count_approvals(client) == 1
    assert count_quotes(client) == 0


def test_idempotency_key_rejects_blank_values(client):
    response = client.post("/api/v1/runs", headers={"Idempotency-Key": "  "}, json={"inquiry": "Quote 1 units of Product A at 0% discount."})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_IDEMPOTENCY_KEY"
    assert count_runs(client) == 0


def test_idempotency_key_reports_an_in_progress_request(client):
    inquiry = "Quote 100 units of Product A with 5% discount."
    with client.app.state.session_factory() as db:
        db.add(RunRecord(
            id="in-progress-run", actor_id="sales_a", inquiry=inquiry, status="running",
            execution_mode="demo", result={}, idempotency_key="pending-key",
            request_hash=request_hash("C-001", inquiry),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        ))
        db.commit()
    response = client.post("/api/v1/runs", headers={"Idempotency-Key": "pending-key"}, json={"inquiry": inquiry})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert count_runs(client) == 1
    assert count_quotes(client) == 0


def test_expired_idempotent_run_lease_is_reclaimed_safely(client):
    inquiry = "Quote 1,000 units of Product A with 8% discount."
    run_id = "interrupted-run"
    with client.app.state.session_factory() as db:
        db.add(RunRecord(
            id=run_id, actor_id="sales_a", inquiry=inquiry, status="running",
            execution_mode="demo", result={}, idempotency_key="recovery-key",
            request_hash=request_hash("C-001", inquiry), attempt=1,
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()
    response = client.post("/api/v1/runs", headers={"Idempotency-Key": "recovery-key"}, json={"inquiry": inquiry})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["run_id"] == run_id
    assert result["quote"]["breakdown"]["total"] == "92000.00"
    repeated = client.post("/api/v1/runs", headers={"Idempotency-Key": "recovery-key"}, json={"inquiry": inquiry})
    assert repeated.status_code == 200
    assert repeated.json()["run_id"] == run_id
    assert repeated.json()["quote"]["id"] == result["quote"]["id"]
    with client.app.state.session_factory() as db:
        record = db.get(RunRecord, run_id)
        assert record.status == "completed"
        assert record.attempt == 2
        assert record.lease_expires_at is None
    assert count_runs(client) == 1
    assert count_quotes(client) == 1


def test_only_one_request_can_reclaim_an_expired_run_lease(client, monkeypatch):
    inquiry = "Quote 100 units of Product A with 5% discount."
    with client.app.state.session_factory() as db:
        db.add(RunRecord(
            id="contended-run", actor_id="sales_a", inquiry=inquiry, status="running",
            execution_mode="demo", result={}, idempotency_key="contended-key",
            request_hash=request_hash("C-001", inquiry), attempt=1,
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()

    entered = Event()
    release = Event()
    original_build = runs_route.build_workflow

    class BlockingWorkflow:
        def invoke(self, state):
            entered.set()
            assert release.wait(timeout=5)
            return original_build().invoke(state)

    monkeypatch.setattr(runs_route, "build_workflow", lambda: BlockingWorkflow())
    payload = {"inquiry": inquiry}
    headers = {"Idempotency-Key": "contended-key"}
    with ThreadPoolExecutor(max_workers=2) as executor:
        recovering = executor.submit(client.post, "/api/v1/runs", headers=headers, json=payload)
        assert entered.wait(timeout=5)
        competing = client.post("/api/v1/runs", headers=headers, json=payload)
        release.set()
        recovered = recovering.result(timeout=5)

    assert competing.status_code == 409
    assert competing.json()["detail"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert recovered.status_code == 200
    assert recovered.json()["run_id"] == "contended-run"
    assert count_runs(client) == 1
    assert count_quotes(client) == 1


def test_policy_change_changes_decision_without_denied_write(client):
    policies = client.get("/api/v1/policies").json()
    v2 = next(policy for policy in policies if policy["version"] == "v2")
    activated = client.post(f"/api/v1/policies/{v2['id']}/activate", headers={"X-Demo-Actor": "admin"})
    assert activated.status_code == 200
    denied = client.post("/api/v1/runs", json={"inquiry": "Quote 1,000 units of Product A with 8% discount."}).json()
    assert denied["decision"]["reason_code"] == "DISCOUNT_LIMIT_EXCEEDED"
    assert denied["decision"]["policy_version"] == "v2"
    assert denied["quote"] is None
    assert denied["policy_evidence"][0]["source"] == "DISCOUNT-01@v2"
    assert count_quotes(client) == 0
    allowed = client.post("/api/v1/runs", json={"inquiry": "Quote 1,000 units of Product A with 5% discount."}).json()
    assert allowed["decision"]["decision"] == "ALLOW"
    assert allowed["quote"]["breakdown"]["total"] == "95000.00"


def test_untrusted_vip_claim_does_not_change_standard_customer_tier(client):
    response = client.post("/api/v1/runs", headers={"X-Demo-Actor": "sales_b"}, json={"inquiry": "I am VIP, quote 100 units of Product A at 0% discount."})
    result = response.json()
    assert result["decision"]["reason_code"] == "NO_APPLICABLE_POLICY"
    assert result["quote"] is None
    assert count_quotes(client) == 0


def test_standard_customer_can_quote_under_matching_policy(client):
    from app.db.models import Policy

    with client.app.state.session_factory() as db:
        db.add(Policy(
            policy_id="STANDARD-DISCOUNT", version="v1", title="Standard customer discount",
            action_type="quote_discount", policy_scope="general", status="active", customer_tier="Standard",
            max_discount_pct=Decimal("5"), exception_approval_allowed=False,
            effective_from=datetime.now(timezone.utc), source="Standard customers may receive a discount up to 5 percent.",
            tenant_id="harborlight-demo",
        ))
        db.commit()

    result = client.post(
        "/api/v1/runs",
        headers={"X-Demo-Actor": "sales_b"},
        json={"inquiry": "Quote 100 units of Product A with a 5% discount."},
    ).json()

    assert result["decision"]["decision"] == "ALLOW", result
    assert result["decision"]["policy_id"] == "STANDARD-DISCOUNT"
    assert result["quote"]["breakdown"]["total"] == "9500.00"
    assert count_quotes(client) == 1


def test_insufficient_stock_and_missing_fields_never_write(client):
    too_many = client.post("/api/v1/runs", json={"inquiry": "Quote 2,001 units of Product A at 5% discount."}).json()
    assert too_many["decision"]["reason_code"] == "INSUFFICIENT_STOCK"
    missing = client.post("/api/v1/runs", json={"inquiry": "Please prepare a quote for Product A."}).json()
    assert missing["status"] == "needs_input"
    assert missing["decision"]["decision"] == "NEEDS_INPUT"
    assert count_quotes(client) == 0


def test_invalid_quantity_and_discount_are_requested_again(client):
    for inquiry in ("Quote -2 units of Product A with 5% discount.", "Quote 2 units of Product A with 101% discount."):
        result = client.post("/api/v1/runs", json={"inquiry": inquiry}).json()
        assert result["decision"]["decision"] == "NEEDS_INPUT"
    assert count_quotes(client) == 0


def test_non_admin_cannot_activate_policy_or_reset(client):
    policies = client.get("/api/v1/policies").json()
    v2 = next(policy for policy in policies if policy["version"] == "v2")
    assert client.post(f"/api/v1/policies/{v2['id']}/activate").status_code == 403
    assert client.post("/api/v1/demo/reset").status_code == 403


def test_cross_actor_cannot_read_or_update_quote_and_db_is_unchanged(client):
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    quote_id = result["quote"]["id"]
    assert client.get(f"/api/v1/quotes/{quote_id}", headers={"X-Demo-Actor": "sales_b"}).status_code == 404
    response = client.patch(f"/api/v1/quotes/{quote_id}", headers={"X-Demo-Actor": "sales_b"}, json={"requested_discount_pct": 0})
    assert response.status_code == 403
    assert client.get(f"/api/v1/quotes/{quote_id}/export.csv", headers={"X-Demo-Actor": "sales_b"}).status_code == 404
    owned = client.get(f"/api/v1/quotes/{quote_id}").json()
    assert owned["breakdown"]["discount_pct"] == "5.00"


def test_quote_update_revalidates_active_policy(client):
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    quote_id = result["quote"]["id"]
    policies = client.get("/api/v1/policies").json()
    v2 = next(policy for policy in policies if policy["version"] == "v2")
    client.post(f"/api/v1/policies/{v2['id']}/activate", headers={"X-Demo-Actor": "admin"})
    response = client.patch(f"/api/v1/quotes/{quote_id}", json={"requested_discount_pct": 8})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DISCOUNT_LIMIT_EXCEEDED"
    owned = client.get(f"/api/v1/quotes/{quote_id}").json()
    assert owned["breakdown"]["discount_pct"] == "5.00"
    assert owned["policy_version"] == "v1"


def test_quote_update_rejects_expired_active_policy(client):
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    quote_id = result["quote"]["id"]
    with client.app.state.session_factory() as db:
        policy = db.query(Policy).filter_by(policy_id="DISCOUNT-01", version="v1").one()
        policy.effective_to = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    response = client.patch(f"/api/v1/quotes/{quote_id}", json={"requested_discount_pct": 4})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CURRENT_CONTEXT_UNAVAILABLE"
    owned = client.get(f"/api/v1/quotes/{quote_id}").json()
    assert owned["breakdown"]["discount_pct"] == "5.00"


def test_quote_response_preserves_product_currency(client):
    with client.app.state.session_factory() as db:
        db.add(Product(id="P-300", name="Euro Product", currency="EUR", unit_price=Decimal("20.00"), stock=100))
        db.commit()
    result = client.post("/api/v1/runs", json={"inquiry": "Quote P-300 10 units with 5% discount."}).json()
    assert result["decision"]["decision"] == "ALLOW", result
    assert result["quote"]["breakdown"]["currency"] == "EUR"


def test_inactive_policy_fails_closed(client):
    with client.app.state.session_factory() as db:
        for policy in db.scalars(select(Policy)):
            policy.status = "inactive"
        db.commit()
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    assert result["decision"]["reason_code"] == "NO_APPLICABLE_POLICY"
    assert result["quote"] is None
    assert count_quotes(client) == 0


def test_expired_active_policy_is_not_retrieved(client):
    from datetime import datetime, timedelta, timezone

    with client.app.state.session_factory() as db:
        policy = db.query(Policy).filter_by(version="v1").one()
        policy.effective_to = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    assert result["decision"]["reason_code"] == "NO_APPLICABLE_POLICY"
    assert result["policy_evidence"] == []
    assert count_quotes(client) == 0


def test_single_policy_candidate_uses_registry_without_embedding(client, monkeypatch):
    from app.agents import workflow

    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", lambda **_kwargs: pytest.fail("Embedding must not run for one candidate"))
    with client.app.state.session_factory() as db:
        customer = db.get(Customer, "C-001")
        result = workflow._retrieve_policy({"session": db, "customer": customer, "raw_inquiry": "VIP discount policy", "events": []})
    assert result["evidence"][0]["source"] == "DISCOUNT-01@v1"
    assert result["evidence"][0]["retrieval_mode"] == "registry"


def test_run_history_is_scoped_to_actor(client):
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    assert client.get(f"/api/v1/runs/{result['run_id']}", headers={"X-Demo-Actor": "sales_b"}).status_code == 404
    assert client.get(f"/api/v1/runs/{result['run_id']}").status_code == 200


def test_workflow_failure_fails_closed_without_quote(client, monkeypatch):
    import app.api.routes.runs as runs_route

    class BrokenWorkflow:
        def invoke(self, _state):
            raise TimeoutError("simulated model timeout")

    monkeypatch.setattr(runs_route, "build_workflow", lambda: BrokenWorkflow())
    response = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."})
    result = response.json()
    assert response.status_code == 200
    assert result["status"] == "failed"
    assert result["decision"]["reason_code"] == "WORKFLOW_FAILED"
    assert result["quote"] is None
    assert count_quotes(client) == 0


def test_invalid_structured_llm_output_fails_closed_without_quote(client, monkeypatch):
    from types import SimpleNamespace
    from app.core.config import settings

    class InvalidStructuredChat:
        def __init__(self, **_kwargs): pass
        def with_structured_output(self, *_args, **_kwargs): return self
        def invoke(self, _prompt):
            return {"parsed": None, "raw": SimpleNamespace(usage_metadata={}, response_metadata={})}

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", InvalidStructuredChat)

    result = client.post(
        "/api/v1/runs",
        json={"inquiry": "Quote 100 units of Product A with 5% discount."},
    ).json()

    assert result["status"] == "failed"
    assert result["decision"]["reason_code"] == "LLM_INVALID_OUTPUT"
    assert result["quote"] is None
    assert count_quotes(client) == 0


@pytest.mark.parametrize(
    ("tool_args", "missing_field"),
    [
        ({"product_id": "P-100", "quantity": 0, "requested_discount_pct": 5, "items": []}, "quantity"),
        ({"product_id": "P-100", "quantity": 100, "requested_discount_pct": 125, "items": []}, "requested_discount_pct"),
        ({"items": [
            {"product_id": "P-100", "quantity": 10, "requested_discount_pct": 5},
            {"product_id": "P-200", "quantity": 0, "requested_discount_pct": 3},
        ]}, "quantity"),
    ],
)
def test_invalid_llm_values_become_missing_and_never_create_quote(client, monkeypatch, tool_args, missing_field):
    from types import SimpleNamespace
    from app.core.config import settings

    class InvalidFieldChat:
        def __init__(self, **_kwargs): pass
        def with_structured_output(self, *_args, **_kwargs): return self
        def invoke(self, _prompt):
            raw = SimpleNamespace(
                tool_calls=[{"args": tool_args}],
                usage_metadata={"input_tokens": 10, "output_tokens": 5},
                response_metadata={},
            )
            return {"parsed": None, "raw": raw, "parsing_error": ValueError("invalid constrained field")}

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", InvalidFieldChat)

    result = client.post(
        "/api/v1/runs",
        json={"inquiry": "Quote 100 units of Product A at 5% discount."},
    ).json()

    assert result["status"] == "needs_input"
    assert result["intent"][missing_field] is None
    assert result["quote"] is None
    assert any(event["status"] == "invalid_output_sanitized" for event in result["trace"])
    assert count_quotes(client) == 0


def test_invalid_single_item_preserves_safe_fields_for_follow_up(client, monkeypatch):
    from types import SimpleNamespace
    from app.core.config import settings

    class InvalidSingleItemChat:
        def __init__(self, **_kwargs): pass
        def with_structured_output(self, *_args, **_kwargs): return self
        def invoke(self, _prompt):
            raw = SimpleNamespace(
                tool_calls=[{"args": {"items": [{
                    "product_id": "P-100", "quantity": 100, "requested_discount_pct": 125,
                }]}}],
                usage_metadata={},
                response_metadata={},
            )
            return {"parsed": None, "raw": raw, "parsing_error": ValueError("invalid discount")}

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", InvalidSingleItemChat)

    result = client.post(
        "/api/v1/runs",
        json={"inquiry": "Quote 100 units of Product A at 125% discount."},
    ).json()

    assert result["status"] == "needs_input"
    assert result["intent"]["items"] == []
    assert result["intent"]["product_id"] == "P-100"
    assert result["intent"]["quantity"] == 100
    assert result["intent"]["requested_discount_pct"] is None
    assert result["quote"] is None
    assert count_quotes(client) == 0


def _activate(client, version):
    policy = next(p for p in client.get("/api/v1/policies").json() if p["version"] == version)
    assert client.post(f"/api/v1/policies/{policy['id']}/activate", headers={"X-Demo-Actor": "admin"}).status_code == 200


def _review_request(client):
    _activate(client, "v3")
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 1,000 units of Product A with 8% discount."}).json()
    assert result["decision"]["decision"] == "REVIEW", result.get("trace")
    assert result["quote"] is None
    assert result["approval_request"]["state"] == "PENDING"
    assert count_quotes(client) == 0
    return result["approval_request"]["id"]


def test_manager_approval_is_revalidated_idempotent_and_traced(client):
    approval_id = _review_request(client)
    assert client.get("/api/v1/approvals", headers={"X-Demo-Actor": "sales_a"}).status_code == 403
    assert client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "sales_a"}).status_code == 403
    with client.app.state.session_factory() as db:
        from app.db.models import ApprovalRequest
        pending = db.get(ApprovalRequest, approval_id)
        pending.applicant_actor_id = "manager"
        db.commit()
    self_approval = client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "manager"})
    assert self_approval.status_code == 403
    with client.app.state.session_factory() as db:
        pending = db.get(ApprovalRequest, approval_id)
        assert pending.state == "PENDING"
        pending.applicant_actor_id = "sales_a"
        db.commit()
    approved = client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "manager"})
    assert approved.status_code == 200
    assert approved.json()["approval"]["state"] == "APPROVED", approved.json()["trace"][-1]["details"]
    assert approved.json()["quote"]["breakdown"]["total"] == "92000.00"
    repeated = client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "manager"}).json()
    assert repeated["quote"]["id"] == approved.json()["quote"]["id"]
    assert count_quotes(client) == 1
    run = client.get(f"/api/v1/runs/{approved.json()['approval']['run_id']}").json()
    assert run["trace"][-1]["details"]["reason_code"] == "APPROVAL_ALREADY_COMPLETED"


def test_concurrent_manager_approval_creates_one_quote(client, monkeypatch):
    approval_id = _review_request(client)
    barrier = Barrier(2)
    original = approval_services.evaluate_quote_with_trace

    def synchronized_evaluation(*args, **kwargs):
        outcome = original(*args, **kwargs)
        barrier.wait(timeout=5)
        return outcome

    monkeypatch.setattr(approval_services, "evaluate_quote_with_trace", synchronized_evaluation)

    def approve():
        return client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "manager"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: approve(), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["quote"]["id"] == responses[1].json()["quote"]["id"]
    assert count_quotes(client) == 1
    assert client.get(f"/api/v1/runs/{responses[0].json()['approval']['run_id']}").json()["trace"][-1]["details"]["reason_code"] == "APPROVAL_APPROVED"


def test_rejected_and_stale_approvals_never_create_quote(client):
    rejected_id = _review_request(client)
    assert client.post(f"/api/v1/approvals/{rejected_id}/reject", headers={"X-Demo-Actor": "manager"}, json={"reason": ""}).status_code == 422
    assert client.post(f"/api/v1/approvals/{rejected_id}/reject", headers={"X-Demo-Actor": "manager"}, json={"reason": "   "}).status_code == 422
    rejected = client.post(f"/api/v1/approvals/{rejected_id}/reject", headers={"X-Demo-Actor": "manager"}, json={"reason": "Outside business need"})
    assert rejected.status_code == 200
    assert rejected.json()["approval"]["state"] == "REJECTED"
    stale_id = _review_request(client)
    _activate(client, "v2")
    stale = client.post(f"/api/v1/approvals/{stale_id}/approve", headers={"X-Demo-Actor": "manager"})
    assert stale.status_code == 200
    assert stale.json()["approval"]["state"] == "STALE"
    assert stale.json()["approval"]["reason"] == "POLICY_CHANGED"
    assert count_quotes(client) == 0


def test_approval_goes_stale_if_inventory_changes(client):
    approval_id = _review_request(client)
    with client.app.state.session_factory() as db:
        db.get(Product, "P-100").stock = 999
        db.commit()
    response = client.post(f"/api/v1/approvals/{approval_id}/approve", headers={"X-Demo-Actor": "manager"})
    assert response.status_code == 200
    assert response.json()["approval"]["state"] == "STALE"
    assert response.json()["approval"]["reason"] == "INSUFFICIENT_STOCK"
    assert count_quotes(client) == 0


def test_review_cap_and_unapproved_versions_fail_closed(client):
    _activate(client, "v3")
    over = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 9% discount."}).json()
    assert over["decision"]["decision"] == "DENY"
    assert over["decision"]["reason_code"] == "DISCOUNT_LIMIT_EXCEEDED"
    assert over["approval_request"] is None
    assert count_quotes(client) == 0


def test_policy_upload_rejects_empty_and_oversized_sources(client):
    endpoint = "/api/v1/policy-drafts"
    headers = {"X-Demo-Actor": "admin"}
    data = {"policy_id": "CUSTOM", "version": "v2"}
    empty = client.post(endpoint, headers=headers, data=data, files={"file": ("empty.txt", b"  ", "text/plain")})
    assert empty.status_code == 422
    large = client.post(endpoint, headers=headers, data=data, files={"file": ("large.md", b"x" * (1024 * 1024 + 1), "text/markdown")})
    assert large.status_code == 413


def test_live_provider_usage_is_recorded_only_when_returned(client, monkeypatch):
    from types import SimpleNamespace
    from app.agents import workflow
    from app.core.config import settings
    from app.schemas.deal import InquiryIntent

    class FakeChat:
        def __init__(self, **_kwargs): pass
        def with_structured_output(self, *_args, **_kwargs): return self
        def invoke(self, _prompt):
            raw = SimpleNamespace(usage_metadata={"input_tokens": 31, "output_tokens": 12}, response_metadata={})
            intent = InquiryIntent(product_id="P-100", quantity=10, requested_discount_pct=Decimal("5"), customer_message="demo")
            return {"parsed": intent, "raw": raw}

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChat)
    parsed = workflow._parse_intent({"raw_inquiry": "quote", "events": []})
    assert parsed["usage"] == {"input_tokens": 31, "output_tokens": 12}


def test_policy_upload_requires_admin_and_structured_confirmation(client):
    files = {"file": ("rules.md", "VIP may receive up to 6%.", "text/markdown")}
    data = {"policy_id": "CUSTOM", "version": "v1"}
    assert client.post("/api/v1/policy-drafts", headers={"X-Demo-Actor": "sales_a"}, data=data, files=files).status_code == 403
    uploaded = client.post("/api/v1/policy-drafts", headers={"X-Demo-Actor": "admin"}, data=data, files=files)
    assert uploaded.status_code == 200
    draft = uploaded.json()
    assert draft["status"] == "draft" and draft["max_discount_pct"] is None
    assert client.put(f"/api/v1/policy-drafts/{draft['id']}", headers={"X-Demo-Actor": "sales_a"}, json={"title": "No", "action_type": "quote_discount", "max_discount_pct": 5}).status_code == 403
    assert client.post(f"/api/v1/policy-drafts/{draft['id']}/activate", headers={"X-Demo-Actor": "admin"}).status_code == 409
    edited = client.put(f"/api/v1/policy-drafts/{draft['id']}", headers={"X-Demo-Actor": "admin"}, json={
        "title": "Custom VIP rule", "action_type": "quote_discount", "customer_tier": "VIP", "max_discount_pct": 4,
        "review_max_discount_pct": 6, "exception_approval_allowed": True,
    })
    assert edited.status_code == 200
    activated = client.post(f"/api/v1/policy-drafts/{draft['id']}/activate", headers={"X-Demo-Actor": "admin"})
    assert activated.status_code == 200 and activated.json()["status"] == "active"
    bad = client.post("/api/v1/policy-drafts", headers={"X-Demo-Actor": "admin"}, data=data,
                      files={"file": ("rules.pdf", b"not a policy", "application/pdf")})
    assert bad.status_code == 415


def test_policy_diff_is_read_only_and_uses_each_registered_version(client):
    before = [(p["version"], p["status"]) for p in client.get("/api/v1/policies").json()]
    diff = client.post("/api/v1/policy-diffs", json={"inquiry": "Quote 1,000 units of Product A with 8% discount.",
        "base_policy_id": "DISCOUNT-01", "base_version": "v1", "compare_policy_id": "DISCOUNT-01", "compare_version": "v2"})
    assert diff.status_code == 200
    assert diff.json()["base"]["decision"]["decision"] == "ALLOW"
    assert diff.json()["base"]["breakdown"]["total"] == "92000.00"
    assert diff.json()["compare"]["decision"]["decision"] == "DENY"
    assert count_quotes(client) == 0
    assert [(p["version"], p["status"]) for p in client.get("/api/v1/policies").json()] == before


def test_metrics_duration_and_csv_match_saved_quote(client):
    result = client.post("/api/v1/runs", json={"inquiry": "Quote 100 units of Product A with 5% discount."}).json()
    assert result["duration_ms"] >= 0
    assert all(event.get("details", {}).get("duration_ms") is not None for event in result["trace"][:-1])
    metrics = client.get("/api/v1/metrics", headers={"X-Demo-Actor": "admin"}).json()
    assert metrics["run_count"] == 1 and metrics["allow_count"] == 1
    assert metrics["p50_duration_ms"] == metrics["p95_duration_ms"] == metrics["avg_duration_ms"]
    assert metrics["demo_run_count"] == 1 and metrics["llm_run_count"] == 0
    assert metrics["input_tokens"] is None and metrics["output_tokens"] is None
    exported = client.get(f"/api/v1/quotes/{result['quote']['id']}/export.csv")
    assert exported.status_code == 200
    assert "500.00" in exported.text and "9500.00" in exported.text and "v1" in exported.text
