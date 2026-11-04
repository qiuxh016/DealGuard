"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { localizedStatus, useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type QuoteRow = { id: string; status: string; customer_id: string; customer_name: string; product_id: string; quantity: number; currency: string; total: string; policy_id: string; policy_version: string; line_count: number; created_at: string };
type QuoteLine = { product_id: string; product_name: string; currency: string; quantity: number; unit_price: string; discount_pct: string; subtotal: string; discount_amount: string; total: string; policy_id: string; policy_version: string; policy_source: string; citation?: string | null; citation_ref?: string | null; chunk_id?: string | null; guard_evaluation?: { decision?: string; reason_code?: string; checks?: { check: string; result: string }[] } };
type QuoteDetail = { id: string; status: string; customer_id: string; product_id: string; policy_id: string; policy_version: string; created_at: string; breakdown: { currency: string; unit_price: string; quantity: number; discount_pct: string; subtotal: string; discount_amount: string; total: string }; lines: QuoteLine[] };
type Identity = { actor_id: string; role: string };

function money(value: string | number, currency = "USD") {
  return new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: 2 }).format(Number(value));
}

function timestamp(value: string, locale: "zh" | "en") {
  return new Date(value).toLocaleString(locale === "zh" ? "zh-CN" : "en-US", { dateStyle: "medium", timeStyle: "short" });
}

function translatePolicyCitation(policyId: string, version: string, citation: string | null | undefined) {
  if (!citation || policyId !== "SHOW-01-VIP-DISCOUNT") return null;
  const translations: Record<string, string> = {
    v1: "# HarborLight Trading 折扣政策 — v1\n\n经核验的 VIP 客户最高可享 10% 折扣。在此限额内可制作报价草稿。报价仅为草稿，不构成有约束力的订单。",
    v2: "# HarborLight Trading 折扣政策 — v2\n\n经核验的 VIP 客户最高可享 5% 折扣。超过此限额的折扣申请将被拒绝。报价仅为草稿，不构成有约束力的订单。",
    v3: "# HarborLight Trading 折扣政策 — v3\n\n经核验的 VIP 客户，直接折扣上限为 5%。超过 5% 且不高于 8% 的折扣需要演示工作区经理审批。超过 8% 的折扣将被拒绝。报价仅为草稿，不构成有约束力的订单。",
  };
  return translations[version] ?? null;
}

function translateGuardCheck(value: string, locale: "zh" | "en") {
  if (locale !== "zh") return value;
  const labels: Record<string, string> = {
    sales_capability: "销售操作权限", customer_ownership: "客户归属权限", policy_action: "政策授权类型", policy_scope: "政策适用范围",
    customer_tier: "客户等级匹配", policy_rule_complete: "政策规则完整性", quantity: "报价数量有效性", inventory: "库存充足性",
    discount_limit: "折扣限额校验", quote_arithmetic: "报价金额计算",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

function translateGuardResult(value: string, locale: "zh" | "en") {
  if (locale !== "zh") return value;
  return ({ PASS: "通过", FAIL: "未通过", REVIEW: "需审批" } as Record<string, string>)[value] ?? value;
}

export default function QuotesPage() {
  const { locale, t } = useLanguage();
  const [role, setRole] = useState("");
  const [identityChecked, setIdentityChecked] = useState(false);
  const [rows, setRows] = useState<QuoteRow[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<QuoteDetail | null>(null);
  const [search, setSearch] = useState("");
  const [activeFilter, setActiveFilter] = useState<"all" | "draft">("all");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const loadQuotes = useCallback(async (query: string, filter: "all" | "draft") => {
    setBusy(true);
    try {
      const params = new URLSearchParams({ search: query, limit: "50" });
      if (filter === "draft") params.set("status", "DRAFT");
      const response = await fetch(`${API}/api/v1/quotes?${params}`, { credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取报价记录");
      setRows(payload.items); setTotal(payload.total); setNotice("");
      setSelectedId((current) => payload.items.some((row: QuoteRow) => row.id === current) ? current : payload.items[0]?.id ?? "");
    } catch (error) { setRows([]); setTotal(0); setNotice(error instanceof Error ? error.message : "无法读取报价记录"); }
    finally { setBusy(false); }
  }, []);

  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("请先登录后查看报价"); const user: Identity = await response.json(); setRole(user.role); })
      .catch((error) => setNotice(error instanceof Error ? error.message : "请先登录后查看报价"))
      .finally(() => setIdentityChecked(true));
  }, []);

  useEffect(() => {
    if (!identityChecked || notice.startsWith("请先登录")) return;
    const timer = window.setTimeout(() => void loadQuotes(search, activeFilter), 220);
    return () => window.clearTimeout(timer);
  }, [identityChecked, loadQuotes, search, activeFilter, notice]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let cancelled = false;
    void fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { const payload = await response.json(); if (!response.ok) throw new Error(payload?.detail?.message ?? "无法加载报价详情"); return payload as QuoteDetail; })
      .then((payload) => { if (!cancelled) setDetail(payload); })
      .catch((error) => { if (!cancelled) { setDetail(null); setNotice(error instanceof Error ? error.message : "无法加载报价详情"); } });
    return () => { cancelled = true; };
  }, [selectedId]);

  const selected = useMemo(() => rows.find((row) => row.id === selectedId) ?? null, [rows, selectedId]);
  const evidenceGroups = useMemo(() => {
    const groups = new Map<string, QuoteLine[]>();
    for (const line of detail?.lines ?? []) {
      const key = JSON.stringify([line.policy_id, line.policy_version, line.citation_ref ?? "", line.chunk_id ?? "", line.citation ?? ""]);
      groups.set(key, [...(groups.get(key) ?? []), line]);
    }
    return [...groups.values()];
  }, [detail]);
  const currencyMix = useMemo(() => {
    const counts = new Map<string, number>();
    rows.forEach((row) => counts.set(row.currency, (counts.get(row.currency) ?? 0) + 1));
    const values = [...counts.entries()].sort(([currencyA], [currencyB]) => currencyA.localeCompare(currencyB));
    return { values, max: Math.max(1, ...values.map(([, count]) => count)) };
  }, [rows]);
  const exportSelected = async () => {
    if (!selectedId) return;
    try {
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/export.csv`, { credentials: "include" });
      if (!response.ok) throw new Error("报价导出失败");
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `dealguard-${selectedId}.csv`; anchor.click(); URL.revokeObjectURL(url);
    } catch (error) { setNotice(error instanceof Error ? error.message : "报价导出失败"); }
  };

  return (
    <div className="dg-operations">
      <WorkspaceNav role={role} />
      <main className="dg-operations-main">
        <header className="dg-operations-head"><div><h1>{t("报价中心", "Quotes")}</h1><p>{t("查找报价草稿，复核明细、适用政策和 Guard 检查，再导出可追溯记录。", "Review quote drafts, line items, applied policies, and Guard checks, then export traceable records.")}</p></div><div className="dg-operations-actions"><Link className="dg-operations-primary" href="/">{t("新建询盘", "New inquiry")}</Link></div></header>
        {notice && <div className="dg-operations-notice" role="status">{notice} {(notice.startsWith("请先登录") || notice.startsWith("Sign in")) && <Link href="/">{t("返回登录", "Back to sign in")}</Link>}</div>}
        <div className="dg-operations-toolbar"><input className="dg-operations-search" aria-label={t("搜索报价", "Search quotes")} placeholder={t("搜索报价编号、客户或政策编号", "Search by quote, customer, or policy ID")} value={search} onChange={(event) => setSearch(event.target.value)} /></div>
        {currencyMix.values.length > 0 && <section className="dg-quote-mix" aria-label={t("报价币种构成", "Quote currency mix")}>
          <div className="dg-quote-mix-head"><div><h2>{t("报价币种构成", "Quote currency mix")}</h2><p>{t("当前筛选结果 · 按币种分别统计，不合并不同币种金额", "Current filtered results · counts by currency; amounts are not combined across currencies")}</p></div><span>{rows.length} {t("条报价", "quotes")}</span></div>
          <div className="dg-quote-mix-bars">{currencyMix.values.map(([currency, count]) => <div className="dg-quote-mix-row" key={currency}><div><strong>{currency}</strong><span>{count} {t("条", "quotes")}</span></div><div className="dg-quote-mix-track"><i style={{ width: `${count / currencyMix.max * 100}%` }} /></div></div>)}</div>
        </section>}
        <div className="dg-operations-tabs" role="group" aria-label={t("报价状态筛选", "Filter quotes by status")}>
          <button type="button" aria-pressed={activeFilter === "all"} onClick={() => setActiveFilter("all")}>{t("全部报价", "All quotes")}</button>
          <button type="button" aria-pressed={activeFilter === "draft"} onClick={() => setActiveFilter("draft")}>{t("报价草稿", "Drafts")}</button>
        </div>
        <div className="dg-quote-layout">
          <section className="dg-operations-panel dg-quote-list" aria-label="报价列表">
            <div className="dg-quote-list-head"><strong>{t("报价记录", "Quote records")}</strong><span>{busy ? t("正在更新…", "Updating…") : t(`共 ${total} 条`, `${total} total`)}</span></div>
            <div className="dg-quote-table-wrap"><table className="dg-quote-table"><thead><tr><th>{t("报价编号", "Quote ID")}</th><th>{t("客户", "Customer")}</th><th>{t("总额", "Total")}</th><th>{t("政策版本", "Policy version")}</th><th>{t("创建时间", "Created")}</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.id} aria-selected={row.id === selectedId} onClick={() => setSelectedId(row.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedId(row.id); }} tabIndex={0}>
                <td><span className="dg-quote-id">{row.id.slice(0, 8)}</span><div className="dg-quote-subrow">{t(`${row.line_count} 个商品`, `${row.line_count} items`)}</div></td><td>{row.customer_name}<div className="dg-quote-subrow">{row.customer_id}</div></td><td className="dg-quote-amount">{money(row.total, row.currency)}</td><td>{row.policy_id}@{row.policy_version}</td><td>{timestamp(row.created_at, locale)}</td>
              </tr>)}</tbody></table>{!rows.length && <div className="dg-operations-empty">{busy ? t("正在加载报价…", "Loading quotes…") : t("没有匹配的报价。创建询盘并获准后，报价草稿会显示在这里。", "No matching quotes. Approved inquiries create quote drafts here.")}</div>}</div>
            <div className="dg-operations-count">{t(`显示最近 ${rows.length} 条 · 当前身份只能读取有权限的报价`, `Showing ${rows.length} recent quotes · Your account only sees quotes it can access`)}</div>
          </section>
          <aside className="dg-operations-panel dg-quote-detail" aria-label="报价详情">
            {!selected || !detail ? <div className="dg-operations-empty">{t("选择一条报价查看可追溯明细", "Select a quote to review its traceable details")}</div> : <>
              <div className="dg-quote-detail-top"><div><h2>{t("报价单", "Quote")} {selected.id.slice(0, 8)}</h2><p className="dg-quote-detail-sub"><span className="dg-status-draft">{localizedStatus(detail.status, locale)}</span> · {t("创建于", "Created")} {timestamp(detail.created_at, locale)}</p></div><button className="dg-operations-secondary" type="button" onClick={() => void exportSelected()}>{t("导出 CSV", "Export CSV")}</button></div>
              <div className="dg-detail-section"><h3>{t("报价信息", "Quote information")}</h3><dl className="dg-detail-meta"><div><dt>{t("客户编号", "Customer ID")}</dt><dd>{detail.customer_id}</dd></div><div><dt>{t("商品项目", "Items")}</dt><dd>{t(`${detail.lines.length || 1} 项`, `${detail.lines.length || 1} items`)}</dd></div><div><dt>{t("适用政策", "Applied policy")}</dt><dd>{detail.policy_id}@{detail.policy_version}</dd></div><div><dt>{t("币种", "Currency")}</dt><dd>{detail.breakdown.currency}</dd></div></dl></div>
              <div className="dg-detail-section"><h3>{t("报价明细", "Line items")}</h3><div className="dg-quote-table-wrap"><table className="dg-quote-line-table"><thead><tr><th>{t("商品", "Product")}</th><th>{t("单价", "Unit price")}</th><th>{t("数量", "Quantity")}</th><th>{t("折扣", "Discount")}</th><th>{t("行金额", "Line total")}</th></tr></thead><tbody>
                {(detail.lines.length ? detail.lines : [{ product_id: detail.product_id, product_name: detail.product_id, currency: detail.breakdown.currency, quantity: detail.breakdown.quantity, unit_price: detail.breakdown.unit_price, discount_pct: detail.breakdown.discount_pct, subtotal: detail.breakdown.subtotal, discount_amount: detail.breakdown.discount_amount, total: detail.breakdown.total, policy_id: detail.policy_id, policy_version: detail.policy_version, policy_source: "" }]).map((line, index) => <tr key={`${line.product_id}-${index}`}><td><span className="dg-quote-product-id">{line.product_id}</span><div className="dg-quote-subrow">{line.product_name}</div></td><td>{money(line.unit_price, line.currency)}</td><td>{line.quantity.toLocaleString()}</td><td><span className="dg-quote-discount">{line.discount_pct}%</span><div className="dg-quote-subrow">−{money(line.discount_amount, line.currency)}</div></td><td className="dg-quote-line-total">{money(line.total, line.currency)}</td></tr>)}
              </tbody></table></div><div className="dg-quote-totals"><div><span>{t("折前金额", "Subtotal")}</span><strong>{money(detail.breakdown.subtotal, detail.breakdown.currency)}</strong></div><div><span>{t("折扣金额", "Discount")}</span><strong>−{money(detail.breakdown.discount_amount, detail.breakdown.currency)}</strong></div><div className="dg-quote-total"><span>{t("报价总额", "Quote total")}</span><strong>{money(detail.breakdown.total, detail.breakdown.currency)}</strong></div></div></div>
              {evidenceGroups.map((group, index) => { const line = group[0]; const translatedCitation = locale === "zh" ? translatePolicyCitation(line.policy_id, line.policy_version, line.citation) : null; const products = [...new Map(group.map((item) => [item.product_id, item])).values()]; return <div className="dg-detail-section" key={`evidence-${line.policy_id}-${line.policy_version}-${line.citation_ref ?? index}`}><h3>{t("政策依据", "Policy evidence")} · {line.policy_id}@{line.policy_version}</h3><div className="dg-evidence-box"><strong>{line.citation_ref || t("未保存条款引用", "No clause reference saved")}</strong>{line.chunk_id && <code>{t("政策分块编号", "Policy chunk ID")}: {line.chunk_id}</code>}<div className="dg-evidence-products"><span>{t("适用商品", "Applies to")}</span>{products.map((item) => <span className="dg-evidence-product" key={item.product_id}>{item.product_id} · {item.product_name}</span>)}</div>{line.citation ? <>{translatedCitation ? <><p>{translatedCitation}</p>{locale === "zh" && <details className="dg-policy-original"><summary>查看政策英文原文</summary><p>{line.citation}</p></details>}</> : <><p>{line.citation}</p>{locale === "zh" && <p className="dg-policy-translation-note">该政策暂无中文译文，以下保留原文，避免改变政策含义。</p>}</>}</> : <p>{t("该报价未包含可展示的精确条款引用。", "This quote has no exact clause citation to display.")}</p>}</div></div>; })}
              {detail.lines.map((line, index) => line.guard_evaluation?.checks?.length ? <div className="dg-detail-section" key={`guard-${line.product_id}-${index}`}><details className="dg-guard-details"><summary>{t(`查看 Guard 检查 · ${line.product_id}（${line.guard_evaluation.checks.length}）`, `View Guard checks · ${line.product_id} (${line.guard_evaluation.checks.length})`)}</summary><ul>{line.guard_evaluation.checks.map((check, checkIndex) => <li key={checkIndex}><span>{translateGuardCheck(check.check, locale)}</span><strong>{translateGuardResult(check.result, locale)}</strong></li>)}</ul></details></div> : null)}
            </>}
          </aside>
        </div>
      </main>
    </div>
  );
}
