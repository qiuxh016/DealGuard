from io import BytesIO

from openpyxl import load_workbook


ADMIN = {"X-Demo-Actor": "admin"}


def _csv_file(text: str):
    return {"file": ("products.csv", text.encode("utf-8-sig"), "text/csv")}


def test_admin_can_download_templates_preview_and_import_csv_atomically(client):
    csv_template = client.get("/api/v1/products/import/template?format=csv", headers=ADMIN)
    assert csv_template.status_code == 200
    assert "sku,name,currency,unit_price,stock" in csv_template.text

    xlsx_template = client.get("/api/v1/products/import/template?format=xlsx", headers=ADMIN)
    workbook = load_workbook(BytesIO(xlsx_template.content), read_only=True)
    assert tuple(next(workbook.active.iter_rows(values_only=True))) == ("sku", "name", "currency", "unit_price", "stock")
    workbook.close()

    initial = client.get("/api/v1/products", headers=ADMIN).json()
    file = _csv_file("sku,name,currency,unit_price,stock\nP-301,New Pump,USD,12.50,120\nP-302,New Valve,CNY,8,40\n")
    preview = client.post("/api/v1/products/import/preview", files=file, headers=ADMIN)
    assert preview.status_code == 200, preview.text
    assert preview.json()["total_rows"] == 2
    assert preview.json()["valid_rows"] == 2
    assert preview.json()["invalid_rows"] == 0
    assert client.get("/api/v1/products", headers=ADMIN).json() == initial

    imported = client.post("/api/v1/products/import", files=file, headers=ADMIN)
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported_count"] == 2
    assert {product["id"] for product in client.get("/api/v1/products", headers=ADMIN).json()} >= {"P-301", "P-302"}


def test_invalid_or_duplicate_rows_block_the_whole_import(client):
    file = _csv_file("sku,name,currency,unit_price,stock\nP-303,Valid Item,USD,5.25,10\nP-100,Duplicate Existing,USD,3,5\nP-304,Bad Price,USD,free,7\nP-306,Overflow Price,USD,1E+12,2147483648\n")
    preview = client.post("/api/v1/products/import/preview", files=file, headers=ADMIN)
    assert preview.status_code == 200
    assert preview.json()["valid_rows"] == 1
    assert preview.json()["invalid_rows"] == 3
    assert "当前工作区已存在此 SKU，不会覆盖现有商品。" in preview.json()["rows"][1]["errors"]
    assert any("单价" in error for error in preview.json()["rows"][2]["errors"])
    assert any("单价" in error for error in preview.json()["rows"][3]["errors"])
    assert any("库存" in error for error in preview.json()["rows"][3]["errors"])

    imported = client.post("/api/v1/products/import", files=file, headers=ADMIN)
    assert imported.status_code == 422
    assert imported.json()["detail"]["code"] == "PRODUCT_IMPORT_VALIDATION_FAILED"
    product_ids = {product["id"] for product in client.get("/api/v1/products", headers=ADMIN).json()}
    assert "P-303" not in product_ids


def test_excel_import_and_role_scope(client):
    workbook = load_workbook(BytesIO(client.get("/api/v1/products/import/template?format=xlsx", headers=ADMIN).content))
    workbook.active.append(("P-305", "Excel Product", "EUR", 19.95, 8))
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    file = {"file": ("products.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}

    sales_response = client.post("/api/v1/products/import/preview", files=file, headers={"X-Demo-Actor": "sales_a"})
    assert sales_response.status_code == 403
    preview = client.post("/api/v1/products/import/preview", files=file, headers=ADMIN)
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"][0]["unit_price"] == "19.95"
    imported = client.post("/api/v1/products/import", files=file, headers=ADMIN)
    assert imported.status_code == 200, imported.text
    assert imported.json()["products"][0]["id"] == "P-305"
