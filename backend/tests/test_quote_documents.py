def _create_quote(client):
    response = client.post("/api/v1/runs", json={"inquiry": "Please quote 1,000 units of Product A at 8% discount."})
    assert response.status_code == 200
    return response.json()["quote"]


def test_quote_pdf_and_customer_email_are_rendered_from_saved_quote(client):
    quote = _create_quote(client)
    before = client.get("/api/v1/quotes", headers={"X-Demo-Actor": "admin"}).json()["total"]

    pdf = client.get(f"/api/v1/quotes/{quote['id']}/quotation.pdf?locale=en")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert f"{quote['id']}.pdf" in pdf.headers["content-disposition"]

    draft = client.get(f"/api/v1/quotes/{quote['id']}/email-draft?locale=en")
    assert draft.status_code == 200
    assert draft.json()["subject"].startswith("Quotation draft")
    assert "Product A" in draft.json()["body"]
    assert "USD 92,000.00" in draft.json()["body"]
    assert "does not create a binding order" in draft.json()["body"]
    after = client.get("/api/v1/quotes", headers={"X-Demo-Actor": "admin"}).json()["total"]
    assert after == before


def test_quote_document_endpoints_preserve_customer_ownership_and_locale(client):
    quote = _create_quote(client)

    forbidden_pdf = client.get(f"/api/v1/quotes/{quote['id']}/quotation.pdf", headers={"X-Demo-Actor": "sales_b"})
    forbidden_email = client.get(f"/api/v1/quotes/{quote['id']}/email-draft", headers={"X-Demo-Actor": "sales_b"})
    assert forbidden_pdf.status_code == forbidden_email.status_code == 404

    chinese_email = client.get(f"/api/v1/quotes/{quote['id']}/email-draft?locale=zh")
    chinese_pdf = client.get(f"/api/v1/quotes/{quote['id']}/quotation.pdf?locale=zh")
    assert chinese_email.status_code == 200
    assert "报价草稿" in chinese_email.json()["subject"]
    assert "报价草稿" in chinese_email.json()["body"]
    assert chinese_pdf.status_code == 200 and chinese_pdf.content.startswith(b"%PDF-")
    assert client.get(f"/api/v1/quotes/{quote['id']}/email-draft?locale=fr").status_code == 422
