from html import escape
from io import BytesIO
from decimal import Decimal
from typing import Literal

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.db.models import Customer, QuoteDraft

Locale = Literal["en", "zh"]


def _money(value: object, currency: str) -> str:
    return f"{currency} {Decimal(str(value)):,.2f}"


def _lines(quote: QuoteDraft) -> list[dict]:
    if quote.lines:
        return quote.lines
    return [{
        "product_id": quote.product_id,
        "product_name": quote.product_id,
        "currency": "USD",
        "quantity": quote.quantity,
        "unit_price": str(quote.unit_price),
        "discount_pct": str(quote.discount_pct),
        "subtotal": str(quote.subtotal),
        "discount_amount": str(quote.discount_amount),
        "total": str(quote.total),
        "policy_id": quote.policy_id,
        "policy_version": quote.policy_version,
    }]


def email_draft(quote: QuoteDraft, customer: Customer, locale: Locale) -> dict[str, str]:
    lines = _lines(quote)
    currency = lines[0].get("currency", "USD")
    issued = quote.status == "ISSUED"
    if locale == "zh":
        line_text = "\n".join(
            f"- {line['product_id']} · {line.get('product_name', line['product_id'])}："
            f"{int(line['quantity']):,} 件，折扣 {line['discount_pct']}%，"
            f"金额 {_money(line['total'], line['currency'])}"
            for line in lines
        )
        return {
            "quote_id": quote.id,
            "locale": locale,
            "subject": f"报价草稿 {quote.id[:8]} · {customer.name}",
            "body": (
                f"您好，{customer.name}：\n\n感谢您的询盘。以下为{ '已发布报价' if issued else '待审核报价草稿' }。邮件草稿尚未发送，报价不构成有约束力的订单。\n\n"
                f"{line_text}\n\n报价总额：{_money(quote.total, currency)}\n"
                f"政策依据：{quote.policy_id}@{quote.policy_version}\n\n如需调整数量或报价条件，请回复此邮件。"
            ),
        }
    line_text = "\n".join(
        f"- {line['product_id']} - {line.get('product_name', line['product_id'])}: "
        f"{int(line['quantity']):,} units at {line['discount_pct']}% discount, "
        f"{_money(line['total'], line['currency'])}"
        for line in lines
    )
    return {
        "quote_id": quote.id,
        "locale": locale,
        "subject": f"Quotation draft {quote.id[:8]} - {customer.name}",
        "body": (
            f"Dear {customer.name},\n\nThank you for your inquiry. Please review the {'issued quotation' if issued else 'quotation draft'} below. "
            f"This email draft has not been sent and the quotation does not create a binding order.\n\n"
            f"{line_text}\n\nQuote total: {_money(quote.total, currency)}\n"
            f"Policy reference: {quote.policy_id}@{quote.policy_version}\n\n"
            "Please reply if you would like to discuss quantities or other terms.\n\nBest regards"
        ),
    }


def quotation_pdf(quote: QuoteDraft, customer: Customer, locale: Locale) -> bytes:
    font_name = "Helvetica"
    lines = _lines(quote)
    has_non_ascii = any(ord(char) > 127 for text in [customer.name, *(line.get("product_name", "") for line in lines)] for char in text)
    if locale == "zh" or has_non_ascii:
        font_name = "STSong-Light"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))

    labels = {
        "en": {"title": "Quotation draft", "customer": "Customer", "quote": "Quote ID", "status": "Status", "draft": "DRAFT - NOT ISSUED", "product": "Product", "quantity": "Quantity", "unit_price": "Unit price", "discount": "Discount", "line_total": "Line total", "subtotal": "Subtotal", "discount_amount": "Discount", "total": "Quote total", "policy": "Policy basis", "disclaimer": "Draft for review only. This document is not a binding offer or order."},
        "zh": {"title": "报价草稿", "customer": "客户", "quote": "报价编号", "status": "状态", "draft": "草稿 · 尚未正式发出", "product": "商品", "quantity": "数量", "unit_price": "单价", "discount": "折扣", "line_total": "行金额", "subtotal": "折前金额", "discount_amount": "折扣金额", "total": "报价总额", "policy": "政策依据", "disclaimer": "本文件仅供审核，不构成正式要约或订单。"},
    }[locale]
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("DGTitle", parent=styles["Title"], fontName=font_name, fontSize=20, textColor=colors.HexColor("#172b4d"), alignment=0)
    body_style = ParagraphStyle("DGBody", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=14, textColor=colors.HexColor("#40536b"))
    header_style = ParagraphStyle("DGHeader", parent=body_style, textColor=colors.white, fontSize=8, leading=10)
    right_style = ParagraphStyle("DGRight", parent=body_style, alignment=TA_RIGHT)

    def p(value: object, style: ParagraphStyle = body_style) -> Paragraph:
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

    currency = lines[0].get("currency", "USD")
    status_labels = {
        "en": {"DRAFT": "DRAFT - NOT ISSUED", "REVISED": "REVISED DRAFT - NOT ISSUED", "ISSUED": "ISSUED - NON-BINDING", "ACCEPTED": "CUSTOMER ACCEPTED", "REJECTED": "CUSTOMER DECLINED", "EXPIRED": "EXPIRED"},
        "zh": {"DRAFT": "草稿 · 尚未正式发出", "REVISED": "修订草稿 · 尚未正式发出", "ISSUED": "已发布 · 不构成订单", "ACCEPTED": "客户已接受", "REJECTED": "客户已拒绝", "EXPIRED": "已过期"},
    }[locale]
    status_text = status_labels.get(quote.status, quote.status)
    story = [p(labels["title"], title_style), Spacer(1, 12)]
    metadata = [
        [p(labels["quote"]), p(quote.id), p(labels["status"]), p(status_text)],
        [p(labels["customer"]), p(customer.name), p("Date" if locale == "en" else "日期"), p(quote.created_at.strftime("%Y-%m-%d"))],
    ]
    meta = Table(metadata, colWidths=[70, 190, 55, 160])
    meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f6fa")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe3ed")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dbe3ed")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [meta, Spacer(1, 18)]
    header = [labels[key] for key in ("product", "unit_price", "quantity", "discount", "line_total")]
    data = [[p(value, header_style) for value in header]]
    for line in lines:
        product = f"{line['product_id']}\n{line.get('product_name', line['product_id'])}"
        data.append([
            p(product), p(_money(line["unit_price"], currency)),
            p(f"{int(line['quantity']):,}"), p(f"{line['discount_pct']}%"),
            p(_money(line["total"], currency), right_style),
        ])
    table = Table(data, colWidths=[170, 90, 62, 58, 95], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3157c8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dbe3ed")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [table, Spacer(1, 14)]
    totals = [
        [p(labels["subtotal"]), p(_money(quote.subtotal, currency), right_style)],
        [p(labels["discount_amount"]), p(f"-{_money(quote.discount_amount, currency)}", right_style)],
        [p(labels["total"]), p(_money(quote.total, currency), right_style)],
    ]
    total_table = Table(totals, colWidths=[370, 105], hAlign="RIGHT")
    total_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#eaf0ff")),
        ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#203d87")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [total_table, Spacer(1, 18), p(f"{labels['policy']}: {quote.policy_id}@{quote.policy_version}")]
    citations = list(dict.fromkeys(line.get("citation", "") for line in lines if line.get("citation")))
    for citation in citations:
        story.append(p(citation))
    story += [Spacer(1, 14), p(labels["disclaimer"])]

    output = BytesIO()
    SimpleDocTemplate(output, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=42, bottomMargin=42, title=f"DealGuard {labels['title']} {quote.id}").build(story)
    return output.getvalue()
