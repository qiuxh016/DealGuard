from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path
import re
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

CUSTOMER_IMPORT_HEADERS = ("customer_id", "name", "tier")
MAX_CUSTOMER_IMPORT_BYTES = 5 * 1024 * 1024
MAX_CUSTOMER_IMPORT_ROWS = 2000


class CustomerImportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def customer_import_template(format_name: str) -> tuple[bytes, str, str]:
    if format_name == "csv":
        content = "\ufeff" + ",".join(CUSTOMER_IMPORT_HEADERS) + "\r\n"
        return content.encode("utf-8"), "text/csv; charset=utf-8", "dealguard-customers-template.csv"
    if format_name != "xlsx":
        raise CustomerImportError("仅支持 CSV 或 XLSX 模板。")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Customers"
    sheet.append(CUSTOMER_IMPORT_HEADERS)
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245BD8")
    for column, width in zip("ABC", (22, 34, 18), strict=True):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "dealguard-customers-template.xlsx"


def _read_rows(content: bytes, filename: str) -> list[list[Any]]:
    if not content:
        raise CustomerImportError("上传文件为空。")
    if len(content) > MAX_CUSTOMER_IMPORT_BYTES:
        raise CustomerImportError("文件不能超过 5 MiB。", status_code=413)
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        try:
            return list(csv.reader(StringIO(content.decode("utf-8-sig"), newline="")))
        except (UnicodeDecodeError, csv.Error) as error:
            raise CustomerImportError("CSV 必须是有效的 UTF-8 文件。") from error
    if extension == ".xlsx":
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            sheet = workbook.active
            if sheet.max_row and sheet.max_row - 1 > MAX_CUSTOMER_IMPORT_ROWS:
                workbook.close()
                raise CustomerImportError(f"每次最多导入 {MAX_CUSTOMER_IMPORT_ROWS} 行客户。", status_code=413)
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            workbook.close()
            return rows
        except CustomerImportError:
            raise
        except Exception as error:
            raise CustomerImportError("无法读取 XLSX 文件；请使用有效的 Excel 工作簿。") from error
    raise CustomerImportError("仅支持 .csv 和 .xlsx 文件。")


def parse_customer_import(content: bytes, filename: str) -> list[dict[str, Any]]:
    rows = _read_rows(content, filename)
    if not rows:
        raise CustomerImportError("文件中没有表头。")
    headers = [str(value or "").strip().lower() for value in rows[0]]
    if len(headers) != len(set(headers)) or tuple(headers) != CUSTOMER_IMPORT_HEADERS:
        raise CustomerImportError("表头必须按顺序包含 customer_id、name、tier。")
    result = []
    for row_number, raw in enumerate(rows[1:], start=2):
        if not any(str(value or "").strip() for value in raw):
            continue
        if len(result) >= MAX_CUSTOMER_IMPORT_ROWS:
            raise CustomerImportError(f"每次最多导入 {MAX_CUSTOMER_IMPORT_ROWS} 行客户。", status_code=413)
        values = [str(raw[index] if index < len(raw) and raw[index] is not None else "").strip() for index in range(3)]
        customer_id, name, tier = values
        customer_id = customer_id.upper()
        errors = []
        if not re.fullmatch(r"[A-Z0-9_-]{2,80}", customer_id):
            errors.append("客户编号必须为 2–80 位字母、数字、下划线或连字符。")
        if not 2 <= len(name) <= 160:
            errors.append("客户名称必须为 2–160 个字符。")
        if tier not in {"VIP", "Standard"}:
            errors.append("客户等级必须为 VIP 或 Standard。")
        result.append({
            "row_number": row_number, "customer_id": customer_id or None,
            "name": name or None, "tier": tier or None, "errors": errors,
            "customer": {"id": customer_id, "name": name, "tier": tier} if not errors else None,
        })

    duplicates: dict[str, list[dict[str, Any]]] = {}
    for row in result:
        if row["customer_id"]:
            duplicates.setdefault(row["customer_id"], []).append(row)
    for repeated in duplicates.values():
        if len(repeated) > 1:
            for row in repeated:
                row["errors"].append("此文件中存在重复客户编号。")
                row["customer"] = None
    return result


def public_customer_import_preview(rows: list[dict[str, Any]], existing_ids: set[str]) -> dict[str, Any]:
    preview_rows = []
    for row in rows:
        errors = list(row["errors"])
        if row["customer_id"] and row["customer_id"].upper() in existing_ids:
            errors.append("当前工作区已存在此客户编号，不会覆盖现有客户。")
        preview_rows.append({
            "row_number": row["row_number"], "customer_id": row["customer_id"],
            "name": row["name"], "tier": row["tier"], "errors": errors,
        })
    valid_count = sum(not row["errors"] for row in preview_rows)
    return {
        "total_rows": len(preview_rows), "valid_rows": valid_count,
        "invalid_rows": len(preview_rows) - valid_count, "rows": preview_rows,
    }
