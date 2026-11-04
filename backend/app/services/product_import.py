from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from pathlib import Path
import re
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill


PRODUCT_IMPORT_HEADERS = ("sku", "name", "currency", "unit_price", "stock")
MAX_PRODUCT_IMPORT_BYTES = 5 * 1024 * 1024
MAX_PRODUCT_IMPORT_ROWS = 2000


class ProductImportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def product_import_template(format_name: str) -> tuple[bytes, str, str]:
    if format_name == "csv":
        content = "\ufeff" + ",".join(PRODUCT_IMPORT_HEADERS) + "\r\n"
        return content.encode("utf-8"), "text/csv; charset=utf-8", "dealguard-products-template.csv"
    if format_name != "xlsx":
        raise ProductImportError("仅支持 CSV 或 XLSX 模板。")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    sheet.append(PRODUCT_IMPORT_HEADERS)
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245BD8")
    for column, width in zip("ABCDE", (18, 30, 14, 18, 14), strict=True):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "dealguard-products-template.xlsx"


def _raw_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _read_rows(content: bytes, filename: str) -> list[tuple[int, list[Any]]]:
    if not content:
        raise ProductImportError("上传文件为空。")
    if len(content) > MAX_PRODUCT_IMPORT_BYTES:
        raise ProductImportError("文件不能超过 5 MiB。", status_code=413)
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ProductImportError("CSV 必须使用 UTF-8 编码。") from error
        reader = csv.reader(StringIO(text, newline=""))
        try:
            rows = []
            data_count = 0
            for row_number, row in enumerate(reader, start=1):
                if row_number == 1:
                    rows.append((row_number, row))
                elif any(_raw_text(value) for value in row):
                    data_count += 1
                    if data_count > MAX_PRODUCT_IMPORT_ROWS:
                        raise ProductImportError(f"每次最多导入 {MAX_PRODUCT_IMPORT_ROWS} 行商品。", status_code=413)
                    rows.append((row_number, row))
            return rows
        except csv.Error as error:
            raise ProductImportError("CSV 格式无效，请检查引号和分隔符。") from error
    if extension == ".xlsx":
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            sheet = workbook.active
            if sheet.max_row and sheet.max_row - 1 > MAX_PRODUCT_IMPORT_ROWS:
                workbook.close()
                raise ProductImportError(f"每次最多导入 {MAX_PRODUCT_IMPORT_ROWS} 行商品。", status_code=413)
            rows = []
            for row_number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                row = list(values)
                if row_number == 1 or any(_raw_text(value) for value in row):
                    rows.append((row_number, row))
            workbook.close()
            return rows
        except ProductImportError:
            raise
        except Exception as error:
            raise ProductImportError("无法读取 XLSX 文件；请使用有效的 Excel 工作簿。") from error
    raise ProductImportError("仅支持 .csv 和 .xlsx 文件。")


def parse_product_import(content: bytes, filename: str) -> list[dict[str, Any]]:
    rows = _read_rows(content, filename)
    if not rows:
        raise ProductImportError("文件中没有表头。")
    header_row_number, raw_headers = rows[0]
    headers = [_raw_text(value).lower() for value in raw_headers]
    if len(headers) != len(set(headers)):
        raise ProductImportError(f"第 {header_row_number} 行存在重复列名。")
    missing = [header for header in PRODUCT_IMPORT_HEADERS if header not in headers]
    extra = [header for header in headers if header and header not in PRODUCT_IMPORT_HEADERS]
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少列：{', '.join(missing)}")
        if extra:
            details.append(f"模板不支持额外列：{', '.join(extra)}")
        raise ProductImportError("表头不符合模板，" + "；".join(details) + "。")
    column_indexes = {name: headers.index(name) for name in PRODUCT_IMPORT_HEADERS}
    result: list[dict[str, Any]] = []

    for row_number, raw_values in rows[1:]:
        if not any(_raw_text(value) for value in raw_values):
            continue
        if len(result) >= MAX_PRODUCT_IMPORT_ROWS:
            raise ProductImportError(f"每次最多导入 {MAX_PRODUCT_IMPORT_ROWS} 行商品。", status_code=413)

        def value_for(name: str) -> Any:
            index = column_indexes[name]
            return raw_values[index] if index < len(raw_values) else None

        sku_raw = _raw_text(value_for("sku"))
        sku = sku_raw.upper()
        name = _raw_text(value_for("name"))
        currency = _raw_text(value_for("currency")).upper()
        price_raw = _raw_text(value_for("unit_price"))
        stock_raw = _raw_text(value_for("stock"))
        errors: list[str] = []

        if not re.fullmatch(r"[A-Z0-9_-]{2,80}", sku):
            errors.append("SKU 必须为 2–80 位字母、数字、下划线或连字符。")
        if not 2 <= len(name) <= 160:
            errors.append("商品名称必须为 2–160 个字符。")
        if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
            errors.append("币种必须是 3 位英文字母代码，例如 USD。")

        price: Decimal | None = None
        try:
            price = Decimal(price_raw)
            if (
                not price.is_finite()
                or price <= 0
                or price > Decimal("9999999999.99")
                or price.as_tuple().exponent < -2
            ):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            errors.append("单价必须大于 0，最多 10 位整数且最多 2 位小数。")

        stock: int | None = None
        try:
            stock_decimal = Decimal(stock_raw)
            if (
                not stock_decimal.is_finite()
                or stock_decimal < 0
                or stock_decimal > 2_147_483_647
                or stock_decimal != stock_decimal.to_integral_value()
            ):
                raise InvalidOperation
            stock = int(stock_decimal)
        except (InvalidOperation, ValueError, OverflowError):
            errors.append("库存必须是 0 至 2,147,483,647 之间的整数。")

        product = None
        if not errors and price is not None and stock is not None:
            product = {
                "sku": sku,
                "name": name,
                "currency": currency,
                "unit_price": price,
                "stock": stock,
            }
        result.append({
            "row_number": row_number,
            "sku": sku or None,
            "name": name or None,
            "currency": currency or None,
            "unit_price": format(price, ".2f") if price is not None and price.is_finite() else price_raw or None,
            "stock": stock if stock is not None else stock_raw or None,
            "errors": errors,
            "product": product,
        })

    sku_rows: dict[str, list[dict[str, Any]]] = {}
    for row in result:
        if row["sku"]:
            sku_rows.setdefault(row["sku"], []).append(row)
    for duplicate_rows in sku_rows.values():
        if len(duplicate_rows) > 1:
            for row in duplicate_rows:
                row["errors"].append("此文件中存在重复 SKU。")
                row["product"] = None
    return result


def public_product_import_preview(rows: list[dict[str, Any]], existing_skus: set[str]) -> dict[str, Any]:
    preview_rows = []
    for row in rows:
        errors = list(row["errors"])
        if row["sku"] and row["sku"].upper() in existing_skus:
            errors.append("当前工作区已存在此 SKU，不会覆盖现有商品。")
        preview_rows.append({
            "row_number": row["row_number"],
            "sku": row["sku"],
            "name": row["name"],
            "currency": row["currency"],
            "unit_price": row["unit_price"],
            "stock": row["stock"],
            "errors": errors,
        })
    valid_count = sum(not row["errors"] for row in preview_rows)
    return {
        "total_rows": len(preview_rows),
        "valid_rows": valid_count,
        "invalid_rows": len(preview_rows) - valid_count,
        "rows": preview_rows,
    }
