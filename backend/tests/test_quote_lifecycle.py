from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import ApprovalRequest, Policy, QuoteAccessToken, QuoteDraft, QuoteRevision, QuoteStatusEvent


def _quote(client, inquiry="Please quote P-100 10 units at 5% discount."):
    response = client.post("/api/v1/runs", json={"inquiry": inquiry})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["decision"]["decision"] == "ALLOW", result
    return result["quote"]


def test_counteroffer_preview_uses_guarded_direct_limit_without_writing(client):
    quote = _quote(client)
    before = client.get("/api/v1/quotes", headers={"X-Demo-Actor": "admin"}).json()["total"]
    result = client.post(f"/api/v1/quotes/{quote['id']}/counteroffer", json={"requested_discount_pct": 12})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["requested_decision"] == "DENY"
    assert body["alternative"]["decision"] == "ALLOW"
    assert body["alternative"]["items"][0]["discount_pct"] == "10.00"
    assert body["alternative"]["items"][0]["total"] == "900.00"
    assert body["writes_performed"] is False
    after = client.get("/api/v1/quotes", headers={"X-Demo-Actor": "admin"}).json()["total"]
    assert after == before


def test_counteroffer_in_review_band_creates_one_existing_manager_approval(client):
    quote = _quote(client)
    with client.app.state.session_factory() as db:
        policy = db.scalar(select(Policy).where(Policy.policy_id == "DISCOUNT-01", Policy.version == "v3"))
        quote_count_before = db.scalar(select(func.count()).select_from(QuoteDraft))
        policy_id = policy.id
    activated = client.post(f"/api/v1/policies/{policy_id}/activate", headers={"X-Demo-Actor": "admin"})
    assert activated.status_code == 200, activated.text

    preview = client.post(f"/api/v1/quotes/{quote['id']}/counteroffer", json={"requested_discount_pct": 7})
    assert preview.status_code == 200, preview.text
    assert preview.json()["requested_decision"] == "REVIEW"
    body = {"requested_discount_pct": 7}
    created = client.post(f"/api/v1/quotes/{quote['id']}/counteroffer/approval", json=body)
    replay = client.post(f"/api/v1/quotes/{quote['id']}/counteroffer/approval", json=body)
    assert created.status_code == replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["approval_id"] == created.json()["approval_id"]
    with client.app.state.session_factory() as db:
        approval = db.get(ApprovalRequest, created.json()["approval_id"])
        assert approval.state == "PENDING"
        assert approval.lines[0]["proposal"]["requested_discount_pct"] == "7"
        assert db.scalar(select(func.count()).select_from(QuoteDraft)) == quote_count_before
        assert db.scalar(select(func.count()).select_from(ApprovalRequest)) == 1

    outcome = client.post(f"/api/v1/approvals/{created.json()['approval_id']}/approve",
                          headers={"X-Demo-Actor": "manager"})
    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["approval"]["state"] == "APPROVED"
    assert outcome.json()["quote"]["breakdown"]["discount_pct"] == "7.00"
    approved_quote_id = outcome.json()["quote"]["id"]
    approved_preview = client.post(f"/api/v1/quotes/{approved_quote_id}/counteroffer", json=body)
    assert approved_preview.status_code == 200, approved_preview.text
    assert approved_preview.json()["approval_already_granted"] is True
    revised = client.post(f"/api/v1/quotes/{approved_quote_id}/revise", json={
        "lines": [{"product_id": "P-100", "quantity": 10, "requested_discount_pct": 7}],
    })
    assert revised.status_code == 200, revised.text
    issued = client.post(f"/api/v1/quotes/{approved_quote_id}/issue")
    assert issued.status_code == 200, issued.text
    history = client.get(f"/api/v1/quotes/{approved_quote_id}/history").json()
    approval_snapshot = history["revisions"][0]["snapshot"]["approvals"]
    assert approval_snapshot[0]["approval_id"] == created.json()["approval_id"]
    assert approval_snapshot[0]["approved_by"] == "manager"


def test_issue_quote_customer_acceptance_and_idempotent_replay(client):
    quote = _quote(client)
    issue = client.post(f"/api/v1/quotes/{quote['id']}/issue")
    assert issue.status_code == 200, issue.text
    issued = issue.json()
    assert issued["status"] == "ISSUED" and issued["version"] == 1
    token = issued["access_token"]

    public = client.get(f"/api/v1/quotes/public/{token}")
    assert public.status_code == 200, public.text
    snapshot = public.json()
    assert snapshot["total"] == "950.00"
    assert snapshot["lines"][0]["product_id"] == "P-100"
    assert "authorization_rule" not in snapshot["lines"][0]
    assert "policy_source" not in snapshot["lines"][0]

    headers = {"Idempotency-Key": "customer-accept-0001"}
    accepted = client.post(f"/api/v1/quotes/public/{token}/response", headers=headers, json={"decision": "ACCEPT"})
    replay = client.post(f"/api/v1/quotes/public/{token}/response", headers=headers, json={"decision": "ACCEPT"})
    assert accepted.status_code == replay.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    assert replay.json()["idempotent_replay"] is True
    assert client.post(f"/api/v1/quotes/public/{token}/response", headers=headers, json={"decision": "REJECT"}).status_code == 409
    assert client.get(f"/api/v1/quotes/public/{token}").json()["status"] == "ACCEPTED"
    assert client.get(f"/api/v1/quotes/{quote['id']}").json()["status"] == "ACCEPTED"
    history = client.get(f"/api/v1/quotes/{quote['id']}/history").json()
    assert [row["to_status"] for row in history["events"][:2]] == ["ACCEPTED", "ISSUED"]


def test_quote_revision_rechecks_policy_and_revokes_old_customer_link(client):
    quote = _quote(client)
    issue = client.post(f"/api/v1/quotes/{quote['id']}/issue").json()
    first_token = issue["access_token"]

    revised = client.post(f"/api/v1/quotes/{quote['id']}/revise", json={
        "lines": [{"product_id": "P-100", "quantity": 20, "requested_discount_pct": 5}],
    })
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "REVISED"
    assert client.get(f"/api/v1/quotes/public/{first_token}").status_code == 404

    second = client.post(f"/api/v1/quotes/{quote['id']}/issue")
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2
    assert client.get(f"/api/v1/quotes/public/{second.json()['access_token']}").json()["total"] == "1900.00"
    history = client.get(f"/api/v1/quotes/{quote['id']}/history").json()
    assert [revision["version"] for revision in history["revisions"]] == [2, 1]
    assert history["revisions"][1]["status"] == "SUPERSEDED"


def test_policy_activation_requires_a_new_guarded_quote_revision_and_keeps_snapshot_immutable(client):
    quote = _quote(client)
    first = client.post(f"/api/v1/quotes/{quote['id']}/issue").json()
    with client.app.state.session_factory() as db:
        first_revision = db.get(QuoteRevision, first["revision_id"])
        original_snapshot = dict(first_revision.snapshot)
        policy = db.scalar(select(Policy).where(Policy.policy_id == "DISCOUNT-01", Policy.version == "v2"))
        policy_row_id = policy.id

    activated = client.post(f"/api/v1/policies/{policy_row_id}/activate", headers={"X-Demo-Actor": "admin"})
    assert activated.status_code == 200, activated.text
    denied = client.post(f"/api/v1/quotes/{quote['id']}/revise", json={
        "lines": [{"product_id": "P-100", "quantity": 10, "requested_discount_pct": 8}],
    })
    assert denied.status_code == 409
    assert client.get(f"/api/v1/quotes/{quote['id']}").json()["status"] == "ISSUED"
    with client.app.state.session_factory() as db:
        assert db.get(QuoteRevision, first["revision_id"]).snapshot == original_snapshot

    revised = client.post(f"/api/v1/quotes/{quote['id']}/revise", json={
        "lines": [{"product_id": "P-100", "quantity": 10, "requested_discount_pct": 5}],
    })
    assert revised.status_code == 200, revised.text
    second = client.post(f"/api/v1/quotes/{quote['id']}/issue")
    assert second.status_code == 200, second.text
    history = client.get(f"/api/v1/quotes/{quote['id']}/history").json()
    assert history["revisions"][0]["snapshot"]["lines"][0]["policy_version"] == "v2"
    assert history["revisions"][1]["snapshot"] == original_snapshot
    assert client.get(f"/api/v1/quotes/public/{first['access_token']}").status_code == 404


def test_policy_change_blocks_revision_and_expired_link_cannot_be_accepted(client):
    quote = _quote(client)
    first = client.post(f"/api/v1/quotes/{quote['id']}/issue").json()
    denied = client.post(f"/api/v1/quotes/{quote['id']}/revise", json={
        "lines": [{"product_id": "P-100", "quantity": 10, "requested_discount_pct": 12}],
    })
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "DISCOUNT_LIMIT_EXCEEDED"
    assert client.get(f"/api/v1/quotes/{quote['id']}").json()["status"] == "ISSUED"

    with client.app.state.session_factory() as db:
        access = db.scalar(select(QuoteAccessToken).where(QuoteAccessToken.revision_id == first["revision_id"]))
        revision = db.get(QuoteRevision, first["revision_id"])
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        access.expires_at = expired
        revision.expires_at = expired
        db.commit()
    response = client.post(f"/api/v1/quotes/public/{first['access_token']}/response",
                          headers={"Idempotency-Key": "customer-expired-0001"}, json={"decision": "ACCEPT"})
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "QUOTE_EXPIRED"
    assert client.get(f"/api/v1/quotes/{quote['id']}").json()["status"] == "EXPIRED"


def test_quote_lifecycle_enforces_owner_and_invalid_response_never_writes(client):
    quote = _quote(client)
    with client.app.state.session_factory() as db:
        revisions_before = db.scalar(select(func.count()).select_from(QuoteRevision))
        events_before = db.scalar(select(func.count()).select_from(QuoteStatusEvent))
    forbidden = client.post(f"/api/v1/quotes/{quote['id']}/issue", headers={"X-Demo-Actor": "sales_b"})
    assert forbidden.status_code == 404
    assert client.post(f"/api/v1/quotes/{quote['id']}/revise", headers={"X-Demo-Actor": "sales_b"}, json={
        "lines": [{"product_id": "P-100", "quantity": 10, "requested_discount_pct": 5}],
    }).status_code == 404
    assert client.get(f"/api/v1/quotes/{quote['id']}/history").json()["revisions"] == []
    assert client.get("/api/v1/quotes/public/invalid-random-token").status_code == 404
    assert client.post("/api/v1/quotes/public/invalid-random-token/response",
                       headers={"Idempotency-Key": "invalid-token-0001"}, json={"decision": "ACCEPT"}).status_code == 404
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(QuoteRevision)) == revisions_before
        assert db.scalar(select(func.count()).select_from(QuoteStatusEvent)) == events_before
