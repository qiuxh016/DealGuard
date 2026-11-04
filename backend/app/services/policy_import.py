from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from pathlib import Path
import re
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from pydantic import ValidationError

from app.schemas.deal import PolicyDraftUpdate


POLICY_IMPORT_HEADERS = (
    "policy_id", "version", "title", "source", "action_type", "policy_scope", "scope_product_id",
    "customer_tier", "max_discount_pct", "review_max_discount_pct", "exception_approval_allowed",
)
MAX_POLICY_IMPORT_BYTES = 5 * 1024 * 1024
MAX_POLICY_IMPORT_ROWS = 500


class PolicyImportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


def policy_import_template(format_name: str) -> tuple[bytes, str, str]:
    if format_name == "csv":
        content = "\ufeff" + ",".join(POLICY_IMPORT_HEADERS) + "\r\n"
        return content.encode("utf-8"), "text/csv; charset=utf-8", "dealguard-policy-template.csv"
    if format_name != "xlsx":
        raise PolicyImportError("仅支持 CSV 或 XLSX 模板。")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Policies"
    sheet.append(POLICY_IMPORT_HEADERS)
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245BD8")
    for column, width in zip("ABCDEFGHIJK", (20, 14, 28, 48, 20, 22, 20, 18, 22, 25, 30), strict=True):
        sheet.column_dimensions[column].width = width
    guide = workbook.create_sheet("Field Guide")
    guide.append(("列名", "填写说明"))
    guide.append(("policy_id", "必填；同一工作区内唯一的政策编号，例如 VIP-DISCOUNT。"))
    guide.append(("version", "必填；不可包含空格，例如 v1。"))
    guide.append(("title", "必填；1–200 个字符。"))
    guide.append(("source", "必填；政策原文，将作为政策检索和引用内容。"))
    guide.append(("action_type", "必填；quote_discount 或 reference_only。"))
    guide.append(("policy_scope", "general、bulk_order、loyalty、early_payment、seasonal_campaign、product_specific；留空按 general。"))
    guide.append(("scope_product_id", "仅 product_specific 范围需要填写当前工作区商品编号。"))
    guide.append(("customer_tier", "VIP 或 Standard；留空按 VIP。"))
    guide.append(("max_discount_pct", "quote_discount 必填；直接折扣上限，0–100。"))
    guide.append(("review_max_discount_pct", "仅启用例外审批时填写，且必须大于直接折扣上限。"))
    guide.append(("exception_approval_allowed", "必填；填写 true 或 false。"))
    guide.column_dimensions["A"].width = 32
    guide.column_dimensions["B"].width = 100
    guide.freeze_panes = "A2"
    for cell in guide[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="245BD8")
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "dealguard-policy-template.xlsx"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _read_rows(content: bytes, filename: str) -> list[tuple[int, list[Any]]]:
    if not content:
        raise PolicyImportError("上传文件为空。")
    if len(content) > MAX_POLICY_IMPORT_BYTES:
        raise PolicyImportError("文件不能超过 5 MiB。", status_code=413)
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        try:
            reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""))
            rows = []
            count = 0
            for number, row in enumerate(reader, 1):
                if number == 1:
                    rows.append((number, row))
                elif any(_text(value) for value in row):
                    count += 1
                    if count > MAX_POLICY_IMPORT_ROWS:
                        raise PolicyImportError(f"每次最多导入 {MAX_POLICY_IMPORT_ROWS} 条政策草稿。", 413)
                    rows.append((number, row))
            return rows
        except UnicodeDecodeError as error:
            raise PolicyImportError("CSV 必须使用 UTF-8 编码。") from error
        except csv.Error as error:
            raise PolicyImportError("CSV 格式无效，请检查引号和分隔符。") from error
    if extension == ".xlsx":
        workbook = None
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            sheet = workbook.active
            if sheet.max_row and sheet.max_row - 1 > MAX_POLICY_IMPORT_ROWS:
                raise PolicyImportError(f"每次最多导入 {MAX_POLICY_IMPORT_ROWS} 条政策草稿。", 413)
            rows = []
            for number, values in enumerate(sheet.iter_rows(values_only=True), 1):
                row = list(values)
                if number == 1 or any(_text(value) for value in row):
                    rows.append((number, row))
            return rows
        except PolicyImportError:
            raise
        except Exception as error:
            raise PolicyImportError("无法读取 XLSX 文件；请使用有效的 Excel 工作簿。") from error
        finally:
            if workbook is not None:
                workbook.close()
    raise PolicyImportError("仅支持 .csv 和 .xlsx 文件。")


def parse_policy_import(content: bytes, filename: str) -> list[dict[str, Any]]:
    rows = _read_rows(content, filename)
    if not rows:
        raise PolicyImportError("文件中没有表头。")
    headers = [_text(value).lower() for value in rows[0][1]]
    if len(headers) != len(set(headers)):
        raise PolicyImportError("表头中存在重复列名。")
    missing = [header for header in POLICY_IMPORT_HEADERS if header not in headers]
    extra = [header for header in headers if header not in POLICY_IMPORT_HEADERS]
    if missing or extra:
        detail = []
        if missing:
            detail.append("缺少列：" + ", ".join(missing))
        if extra:
            detail.append("模板不支持额外列：" + ", ".join(extra))
        raise PolicyImportError("表头不符合模板，" + "；".join(detail) + "。")

    index = {name: headers.index(name) for name in POLICY_IMPORT_HEADERS}
    result: list[dict[str, Any]] = []
    for row_number, raw in rows[1:]:
        if not any(_text(value) for value in raw):
            continue
        if len(result) >= MAX_POLICY_IMPORT_ROWS:
            raise PolicyImportError(f"每次最多导入 {MAX_POLICY_IMPORT_ROWS} 条政策草稿。", 413)
        values = {name: _text(raw[index[name]]) if index[name] < len(raw) else "" for name in POLICY_IMPORT_HEADERS}
        errors: list[str] = []
        policy_id, version = values["policy_id"], values["version"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", policy_id):
            errors.append("政策编号须为 2–80 位字母、数字、下划线或连字符，并以字母或数字开头。")
        if not version or len(version) > 40 or re.search(r"\s", version):
            errors.append("版本号不能为空、不能超过 40 位或包含空白。")
        if not 1 <= len(values["title"]) <= 200:
            errors.append("政策标题必须为 1–200 个字符。")
        if not values["source"]:
            errors.append("政策原文不能为空。")
        action_type = values["action_type"]
        scope = values["policy_scope"] or "general"
        tier = values["customer_tier"] or "VIP"
        if action_type not in {"quote_discount", "reference_only"}:
            errors.append("动作类型须为 quote_discount 或 reference_only。")
        if scope not in {"general", "bulk_order", "loyalty", "early_payment", "seasonal_campaign", "product_specific"}:
            errors.append("政策范围不受支持。")
        if tier not in {"VIP", "Standard"}:
            errors.append("客户等级须为 VIP 或 Standard。")

        def percentage(name: str) -> Decimal | None:
            raw_value = values[name]
            if not raw_value:
                return None
            try:
                number = Decimal(raw_value)
                if not number.is_finite() or number < 0 or number > 100 or number.as_tuple().exponent < -2:
                    raise InvalidOperation
                return number
            except (InvalidOperation, ValueError):
                errors.append(f"{name} 须为 0–100 之间、最多两位小数的数字。")
                return None

        direct = percentage("max_discount_pct")
        review = percentage("review_max_discount_pct")
        boolean_value = values["exception_approval_allowed"].lower()
        bool_map = {"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False}
        exception = bool_map.get(boolean_value)
        if exception is None:
            errors.append("exception_approval_allowed 须填写 true 或 false。")

        update_values = {
            "title": values["title"], "action_type": action_type, "policy_scope": scope,
            "scope_product_id": values["scope_product_id"] or None, "customer_tier": tier,
            "max_discount_pct": direct, "review_max_discount_pct": review,
            "exception_approval_allowed": exception if exception is not None else False,
        }
        try:
            rules = PolicyDraftUpdate.model_validate(update_values)
        except ValidationError as error:
            errors.extend(item["msg"] for item in error.errors())
            rules = None
        draft = None
        if not errors and rules is not None:
            draft = {
                "policy_id": policy_id,
                "version": version,
                "source": values["source"],
                **rules.model_dump(),
            }
        result.append({
            "row_number": row_number,
            "policy_id": policy_id or None,
            "version": version or None,
            "title": values["title"] or None,
            "action_type": action_type or None,
            "policy_scope": scope,
            "customer_tier": tier,
            "errors": errors,
            "draft": draft,
        })

    keys: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in result:
        if row["policy_id"] and row["version"]:
            keys.setdefault((row["policy_id"].upper(), row["version"].upper()), []).append(row)
    for duplicate_rows in keys.values():
        if len(duplicate_rows) > 1:
            for row in duplicate_rows:
                row["errors"].append("此文件中存在重复的政策编号和版本。")
                row["draft"] = None
    return result


def public_policy_import_preview(rows: list[dict[str, Any]], existing_keys: set[tuple[str, str]]) -> dict[str, Any]:
    preview_rows = []
    for row in rows:
        errors = list(row["errors"])
        key = (str(row["policy_id"] or "").upper(), str(row["version"] or "").upper())
        if all(key) and key in existing_keys:
            errors.append("此政策编号和版本已存在，不会覆盖现有政策。")
        preview_rows.append({
            "row_number": row["row_number"], "policy_id": row["policy_id"], "version": row["version"],
            "title": row["title"], "action_type": row["action_type"], "policy_scope": row["policy_scope"],
            "customer_tier": row["customer_tier"], "errors": errors,
        })
    valid_rows = sum(not row["errors"] for row in preview_rows)
    return {"total_rows": len(preview_rows), "valid_rows": valid_rows, "invalid_rows": len(preview_rows) - valid_rows, "rows": preview_rows}
