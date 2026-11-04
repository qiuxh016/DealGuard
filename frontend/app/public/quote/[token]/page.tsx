"use client";

import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useLanguage } from "../../../../components/LanguageProvider";
import "../../../workspace.css";
import "../../../operations.css";
import "./page.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type PublicQuote = {
  quote_id: string; version: number; status: string; customer_name: string; currency: string;
  subtotal: string; discount_amount: string; total: string; issued_at: string; expires_at: string;
  lines: { product_id: string; product_name: string; currency: string; quantity: number; unit_price: string; discount_pct: string; subtotal: string; discount_amount: string; total: string }[];
};

function money(value: string, currency: string) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: 2 }).format(Number(value));
}

export default function PublicQuotePage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const { locale, t } = useLanguage();
  const [quote, setQuote] = useState<PublicQuote | null>(null);
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const idempotencyKeys = useRef<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    void fetch(`${API}/api/v1/quotes/public/${encodeURIComponent(token)}`, { cache: "no-store" })
      .then(async (response) => { const payload = await response.json(); if (!response.ok) throw new Error(payload?.detail?.message ?? t("报价链接无效或已过期。", "This quote link is invalid or expired.")); return payload as PublicQuote; })
      .then((payload) => { if (!cancelled) { setQuote(payload); setStatus(payload.status); } })
      .catch((error) => { if (!cancelled) setNotice(error instanceof Error ? error.message : t("无法读取报价。", "Could not load this quote.")); });
    return () => { cancelled = true; };
  }, [token, t]);

  const respond = async (decision: "ACCEPT" | "REJECT") => {
    setBusy(true); setNotice("");
    idempotencyKeys.current[decision] ??= crypto.randomUUID();
    try {
      const response = await fetch(`${API}/api/v1/quotes/public/${encodeURIComponent(token)}/response`, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKeys.current[decision] },
        body: JSON.stringify({ decision, reason: decision === "REJECT" ? reason : null }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("无法提交报价回复。", "Could not submit your response."));
      setStatus(payload.status); setNotice(payload.idempotent_replay ? t("已收到该报价回复。", "This response was already recorded.") : t("报价回复已记录。", "Your response has been recorded."));
    } catch (error) { setNotice(error instanceof Error ? error.message : t("无法提交报价回复。", "Could not submit your response.")); }
    finally { setBusy(false); }
  };

  const actionable = status === "ISSUED";
  return <main className="dg-public-quote-page"><section className="dg-public-quote-card">
    <div className="dg-public-quote-brand"><span className="dg-workspace-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M12 2.8 20 6v5.5c0 5.2-3.3 8.4-8 10.1-4.7-1.7-8-4.9-8-10.1V6l8-3.2Z" stroke="white" strokeWidth="1.8"/><path d="m8 12 2.5 2.5L16.5 9" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg></span><div><strong>DealGuard</strong><span>{t("安全报价查看", "Secure quotation view")}</span></div><span className="dg-public-quote-badge">{t("报价版本", "Revision")} {quote ? `v${quote.version}` : "—"}</span></div>
    <header><p>{t("客户报价", "Customer quotation")}</p><h1>{quote?.customer_name ?? t("正在加载报价…", "Loading quote…")}</h1><span>{t("报价编号", "Quote ID")} {quote?.quote_id.slice(0, 8) ?? "—"}</span></header>
    {notice && <div className="dg-public-quote-notice" role="status">{notice}</div>}
    {quote && <>
      <div className="dg-public-quote-summary"><div><span>{t("报价总额", "Quote total")}</span><strong>{money(quote.total, quote.currency)}</strong></div><div><span>{t("有效期至", "Valid until")}</span><strong>{new Date(quote.expires_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US", { dateStyle: "medium", timeStyle: "short" })}</strong></div><div><span>{t("报价状态", "Status")}</span><strong>{status === "ISSUED" ? t("待回复", "Awaiting response") : status === "ACCEPTED" ? t("已接受", "Accepted") : t("已拒绝", "Declined")}</strong></div></div>
      <div className="dg-public-quote-table-wrap"><table><thead><tr><th>{t("商品", "Product")}</th><th>{t("单价", "Unit price")}</th><th>{t("数量", "Quantity")}</th><th>{t("折扣", "Discount")}</th><th>{t("金额", "Amount")}</th></tr></thead><tbody>{quote.lines.map((line, index) => <tr key={`${line.product_id}-${index}`}><td><strong>{line.product_id}</strong><span>{line.product_name}</span></td><td>{money(line.unit_price, line.currency)}</td><td>{line.quantity.toLocaleString()}</td><td>{line.discount_pct}%</td><td><strong>{money(line.total, line.currency)}</strong></td></tr>)}</tbody></table></div>
      <div className="dg-public-quote-total"><span>{t("折前金额", "Subtotal")}</span><strong>{money(quote.subtotal, quote.currency)}</strong><span>{t("折扣金额", "Discount")}</span><strong>−{money(quote.discount_amount, quote.currency)}</strong><span className="dg-public-quote-total-final">{t("报价总额", "Quote total")}</span><strong className="dg-public-quote-total-final">{money(quote.total, quote.currency)}</strong></div>
      <p className="dg-public-quote-disclaimer">{t("本报价为限时商务报价，仅用于确认意向；不构成订单，不会触发付款或库存预留。", "This time-limited quotation records commercial intent only. It is not an order and does not reserve inventory or initiate payment.")}</p>
      {actionable ? <div className="dg-public-quote-response"><label>{t("拒绝原因（可选）", "Reason for declining (optional)")}<textarea maxLength={1000} rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label><div><button className="dg-public-quote-reject" type="button" disabled={busy} onClick={() => void respond("REJECT")}>{t("拒绝报价", "Decline quotation")}</button><button className="dg-operations-primary" type="button" disabled={busy} onClick={() => void respond("ACCEPT")}>{busy ? t("提交中…", "Submitting…") : t("接受报价", "Accept quotation")}</button></div></div> : <div className="dg-public-quote-terminal">{status === "ACCEPTED" ? t("您已接受此报价。", "You accepted this quotation.") : t("此报价已关闭，无法继续回复。", "This quotation is closed and can no longer receive a response.")}</div>}
    </>}
    <footer>{t("本页面仅开放此报价版本的查看与确认。", "This page only provides access to this quotation revision.")}</footer>
  </section></main>;
}
