from decimal import Decimal

from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.agents.action_planner import MAX_TOOL_CALLS, MAX_TOOL_ROUNDS, _run_read_tool, plan_read_tools
from app.core.config import settings
from app.db.models import DemoActor, Product, QuoteDraft, Tenant
from app.schemas.deal import InquiryIntent


def _planner_state(client, actor_id="sales_a", product_id=None):
    factory = client.app.state.session_factory
    db = factory()
    return {
        "session": db,
        "actor": db.get(DemoActor, actor_id),
        "customer_id": None,
        "raw_inquiry": "Quote 100 units of Product A with 5% discount.",
        "execution_mode": "llm",
        "intent": InquiryIntent(
            product_id=product_id, quantity=100, requested_discount_pct=Decimal("5"),
            policy_scope="general", customer_message="Quote 100 units of Product A with 5% discount.",
        ).model_dump(mode="json"),
        "events": [],
    }


def test_planner_selects_product_search_and_resolves_only_unique_match(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    calls = [
        AIMessage(content="", tool_calls=[{
            "name": "search_products", "args": {"query": "Product A"}, "id": "search-1", "type": "tool_call",
        }], usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}),
        AIMessage(content="The catalog returned one matching product.", tool_calls=[],
                  usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}),
    ]
    instances = []

    class FakeChat:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            instances.append(self)

        def bind_tools(self, tools):
            self.tools = tools
            return self

        def invoke(self, messages):
            self.messages = messages
            return calls.pop(0)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChat)
    state = _planner_state(client)
    state["usage"] = {"input_tokens": 10, "output_tokens": 20}
    try:
        result = plan_read_tools(state)
    finally:
        state["session"].close()

    assert result["intent"]["product_id"] == "P-100"
    details = result["events"][-1]["details"]
    assert details["tool_calls"] == ["search_products"]
    assert details["product_resolved"] is True
    assert details["write_tools_available"] is False
    assert details["input_tokens"] == 4 and details["output_tokens"] == 1
    assert result["usage"] == {"input_tokens": 14, "output_tokens": 21}
    assert len(calls) == 1
    assert len(instances[0].tools) == 4
    assert len(instances[0].messages[-1].content) > 0


def test_tool_calling_workflow_resolves_catalog_product_then_guard_creates_quote(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    class FakeChat:
        def __init__(self, **_kwargs):
            self.mode = "parse"

        def with_structured_output(self, *_args, **_kwargs):
            self.mode = "parse"
            return self

        def bind_tools(self, _tools):
            self.mode = "planner"
            self.planner_calls = 0
            return self

        def invoke(self, prompt):
            if self.mode == "parse":
                return InquiryIntent(
                    product_id=None, quantity=100, requested_discount_pct=Decimal("5"),
                    policy_scope="general", customer_message="Quote 100 units of Product A with a 5% discount.",
                )
            self.planner_calls += 1
            if self.planner_calls == 1:
                return AIMessage(content="", tool_calls=[{
                    "name": "search_products", "args": {"query": "Product A"}, "id": "search-1", "type": "tool_call",
                }])
            return AIMessage(content="One product matches.", tool_calls=[])

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChat)
    result = client.post(
        "/api/v1/runs",
        headers={"Idempotency-Key": "tool-planner-product-resolution"},
        json={"inquiry": "Quote 100 units of Product A with a 5% discount."},
    )

    assert result.status_code == 200, result.text
    run = result.json()
    assert run["intent"]["product_id"] == "P-100"
    assert run["decision"]["decision"] == "ALLOW"
    assert run["quote"]["breakdown"]["total"] == "9500.00"
    planner_event = next(event for event in run["trace"] if event["step"] == "action_planner")
    assert planner_event["details"]["tool_calls"] == ["search_products"]
    assert planner_event["details"]["write_tools_available"] is False
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == 1


def test_read_tools_are_tenant_scoped_and_reject_write_tools(client):
    state = _planner_state(client)
    state["raw_inquiry"] = "Please quote the Secret Product."
    try:
        with state["session"] as db:
            db.add(Tenant(id="tool-other-tenant", name="Tool isolation"))
            db.flush()
            db.add(Product(
                id="P-SECRET", name="Secret Product", currency="USD", unit_price=Decimal("99.00"),
                stock=50, tenant_id="tool-other-tenant",
            ))
            db.commit()
            state["session"] = db
            search = _run_read_tool("search_products", {"query": "Secret"}, state)
            fabricated_search = _run_read_tool("search_products", {"query": "P-100"}, state)
            inventory = _run_read_tool("check_inventory", {"product_id": "P-SECRET"}, state)
            customer = _run_read_tool("get_customer_profile", {}, state)
            write = _run_read_tool("create_quote", {"product_id": "P-100"}, state)
    finally:
        state["session"].close()

    assert search == {"status": "not_found", "results": [], "truncated": False}
    assert fabricated_search == {"status": "rejected", "reason_code": "PRODUCT_QUERY_NOT_IN_INQUIRY"}
    assert inventory == {"status": "not_found"}
    assert customer["customer_id"] == "C-001"
    assert write["reason_code"] == "TOOL_NOT_ALLOWED"
    with state["session"] as db:
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == 0


def test_planner_stops_at_bounded_tool_call_count(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    class RepeatingChat:
        def __init__(self, **_kwargs):
            pass

        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            type(self).calls.append(1)
            return AIMessage(content="", tool_calls=[{
                "name": "check_inventory", "args": {"product_id": "P-100"},
                "id": f"inventory-{len(type(self).calls)}", "type": "tool_call",
            }])

        calls = []

    monkeypatch.setattr("langchain_openai.ChatOpenAI", RepeatingChat)
    state = _planner_state(client, product_id="P-100")
    try:
        result = plan_read_tools(state)
    finally:
        state["session"].close()

    details = result["events"][-1]["details"]
    assert details["tool_call_count"] <= MAX_TOOL_CALLS
    assert len(RepeatingChat.calls) <= MAX_TOOL_ROUNDS
    assert result["events"][-1]["status"] == "bounded"


def test_fixed_workflow_baseline_can_disable_tool_planning(client, monkeypatch):
    monkeypatch.setattr(settings, "action_planner_enabled", False)
    state = _planner_state(client, product_id="P-100")
    try:
        result = plan_read_tools(state)
    finally:
        state["session"].close()

    assert result["events"][-1]["details"]["planner_mode"] == "fixed_workflow_baseline"
    assert result["events"][-1]["details"]["tool_calls"] == []
