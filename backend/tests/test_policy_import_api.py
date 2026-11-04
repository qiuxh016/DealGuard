from io import BytesIO

from openpyxl import load_workbook

from app.core.config import settings
from app.db.models import Policy, PolicyAuditEvent, PolicyDraft
from app.services.policy_extraction import PolicyRuleExtraction


ADMIN = {"X-Demo-Actor": "admin"}
HEADERS = "policy_id,version,title,source,action_type,policy_scope,scope_product_id,customer_tier,max_discount_pct,review_max_discount_pct,exception_approval_allowed"


def _file(text: str, filename: str = "policies.csv"):
    return {"file": (filename, text.encode("utf-8-sig"), "text/csv")}


def test_policy_import_template_preview_and_atomic_draft_import(client):
    template = client.get("/api/v1/policy-drafts/import/template?format=csv", headers=ADMIN)
    assert template.status_code == 200
    assert HEADERS in template.text

    content = (
        HEADERS + "\n"
        "NEW-DISCOUNT,v1,VIP promotion,VIP discount up to 6 percent.,quote_discount,general,,VIP,6,,false\n"
        "NEW-REFERENCE,v1,Shipping guidance,Please confirm shipping terms.,reference_only,general,,Standard,,,false\n"
    )
    files = _file(content)
    before = client.get("/api/v1/policies", headers=ADMIN).json()
    preview = client.post("/api/v1/policy-drafts/import/preview", files=files, headers=ADMIN)
    assert preview.status_code == 200, preview.text
    assert preview.json()["total_rows"] == preview.json()["valid_rows"] == 2, preview.json()
    with client.app.state.session_factory() as db:
        assert db.query(PolicyDraft).filter(PolicyDraft.policy_id.like("NEW-%")).count() == 0

    imported = client.post("/api/v1/policy-drafts/import", files=files, headers=ADMIN)
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported_count"] == 2
    assert {row["policy_id"] for row in imported.json()["drafts"]} == {"NEW-DISCOUNT", "NEW-REFERENCE"}
    assert all(row["status"] == "draft" for row in imported.json()["drafts"])
    assert client.get("/api/v1/policies", headers=ADMIN).json() == before
    pending = client.get("/api/v1/policy-drafts", headers=ADMIN)
    assert pending.status_code == 200
    assert {row["policy_id"] for row in pending.json()} == {"NEW-DISCOUNT", "NEW-REFERENCE"}


def test_invalid_duplicate_and_product_scope_rows_block_batch(client):
    content = (
        HEADERS + "\n"
        "SAFE-DRAFT,v1,Valid rule,VIP discount up to 4 percent.,quote_discount,general,,VIP,4,,false\n"
        "DISCOUNT-01,v1,Existing version,Existing policy source.,quote_discount,general,,VIP,5,,false\n"
        "UNKNOWN-PRODUCT,v1,Unknown product,Applies to a product.,quote_discount,product_specific,P-404,VIP,5,,false\n"
        "INVALID-LIMIT,v1,Invalid limit,Negative discount.,quote_discount,general,,VIP,-1,,false\n"
    )
    files = _file(content)
    preview = client.post("/api/v1/policy-drafts/import/preview", files=files, headers=ADMIN)
    assert preview.status_code == 200
    assert preview.json()["valid_rows"] == 1, preview.json()
    assert preview.json()["invalid_rows"] == 3
    assert "此政策编号和版本已存在，不会覆盖现有政策。" in preview.json()["rows"][1]["errors"]
    assert "指定商品不在当前工作区。" in preview.json()["rows"][2]["errors"]

    imported = client.post("/api/v1/policy-drafts/import", files=files, headers=ADMIN)
    assert imported.status_code == 422
    assert imported.json()["detail"]["code"] == "POLICY_IMPORT_VALIDATION_FAILED"
    with client.app.state.session_factory() as db:
        assert db.query(PolicyDraft).filter_by(policy_id="SAFE-DRAFT").count() == 0


def test_xlsx_policy_import_and_non_admin_denied(client):
    template = client.get("/api/v1/policy-drafts/import/template?format=xlsx", headers=ADMIN)
    workbook = load_workbook(BytesIO(template.content))
    workbook.active.append(("XLSX-POLICY", "v1", "Excel rule", "VIP discounts capped at five percent.", "quote_discount", "general", None, "VIP", 5, None, False))
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    files = {"file": ("policies.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    denied = client.post("/api/v1/policy-drafts/import/preview", files=files, headers={"X-Demo-Actor": "sales_a"})
    assert denied.status_code == 403
    imported = client.post("/api/v1/policy-drafts/import", files=files, headers=ADMIN)
    assert imported.status_code == 200, imported.text
    assert imported.json()["drafts"][0]["policy_id"] == "XLSX-POLICY"


def test_policy_rule_extraction_returns_reviewable_fields_without_creating_draft(client, monkeypatch):
    def fake_extract(source: str):
        assert "VIP discount up to 6 percent" in source
        return PolicyRuleExtraction(
            title="VIP promotion", action_type="quote_discount", policy_scope="general",
            customer_tier="VIP", max_discount_pct=6, review_max_discount_pct=None,
            exception_approval_allowed=False, supporting_evidence=["VIP discount up to 6 percent"],
            uncertain_fields=["version"],
        )

    monkeypatch.setattr("app.api.routes.policy_tools.extract_policy_rules", fake_extract)
    response = client.post("/api/v1/policy-drafts/extract", headers=ADMIN,
        data={"policy_id": "EXTRACTED", "version": "v1"},
        files={"file": ("policy.md", "VIP discount up to 6 percent", "text/markdown")})
    assert response.status_code == 200, response.text
    assert response.json()["extraction_mode"] == "llm"
    assert response.json()["fields"]["max_discount_pct"] == "6"
    assert response.json()["fields"]["supporting_evidence"] == ["VIP discount up to 6 percent"]
    with client.app.state.session_factory() as db:
        assert db.query(PolicyDraft).filter_by(policy_id="EXTRACTED").count() == 0
        assert db.query(PolicyAuditEvent).filter_by(action="policy_rules_extracted", outcome="SUCCESS").count() == 1


def test_policy_rule_extraction_requires_model_and_human_confirmed_draft_is_not_active(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")
    unavailable = client.post("/api/v1/policy-drafts/extract", headers=ADMIN,
        data={"policy_id": "NO-MODEL", "version": "v1"},
        files={"file": ("policy.md", "VIP discount limit is 5 percent.", "text/markdown")})
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "POLICY_EXTRACTION_MODEL_NOT_CONFIGURED"

    created = client.post("/api/v1/policy-drafts/confirmed", headers=ADMIN, json={
        "policy_id": "CONFIRMED", "version": "v1", "title": "Reviewed policy",
        "source": "VIP discount limit is 5 percent.", "action_type": "quote_discount",
        "policy_scope": "general", "scope_product_id": None, "customer_tier": "VIP",
        "max_discount_pct": 5, "review_max_discount_pct": None, "exception_approval_allowed": False,
    })
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft"
    with client.app.state.session_factory() as db:
        assert db.query(Policy).filter_by(policy_id="CONFIRMED").count() == 0


def test_extractor_filters_non_source_citations_and_marks_missing_required_fields(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "configured-key")
    monkeypatch.setattr(settings, "llm_model", "configured-model")

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return self

        def invoke(self, messages):
            assert messages[0].type == "system"
            return PolicyRuleExtraction(
                title=None, action_type="quote_discount", policy_scope="product_specific",
                scope_product_id=None, customer_tier="VIP", max_discount_pct=None,
                exception_approval_allowed=True, review_max_discount_pct=None,
                supporting_evidence=["Discount cap is 5%.", "Invented evidence"], uncertain_fields=["unknown_field"],
            )

    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda **_kwargs: FakeModel())
    from app.services.policy_extraction import extract_policy_rules

    result = extract_policy_rules("Discount cap is 5%.")
    assert result.supporting_evidence == ["Discount cap is 5%."]
    assert set(result.uncertain_fields) == {"title", "scope_product_id", "max_discount_pct", "review_max_discount_pct"}
