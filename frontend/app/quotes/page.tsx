"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { localizedDecision, localizedStatus, useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type QuoteRow = { id: string; status: string; customer_id: string; customer_name: string; product_id: string; quantity: number; currency: string; total: string; policy_id: string; policy_version: string; line_count: number; created_at: string };
type QuoteLine = { product_id: string; product_name: string; currency: string; quantity: number; unit_price: string; discount_pct: string; subtotal: string; discount_amount: string; total: string; policy_id: string; policy_version: string; policy_source: string; citation?: string | null; citation_ref?: string | null; chunk_id?: string | null; guard_evaluation?: { decision?: string; reason_code?: string; checks?: { check: string; result: string }[] } };
type QuoteDetail = { id: string; status: string; customer_id: string; product_id: string; policy_id: string; policy_version: string; created_at: string; breakdown: { currency: string; unit_price: string; quantity: number; discount_pct: string; subtotal: string; discount_amount: string; total: string }; lines: QuoteLine[] };
type Identity = { actor_id: string; role: string };
type EmailDraft = { quote_id: string; locale: "zh" | "en"; subject: string; body: string };
type LifecycleHistory = { status: string; revisions: { id: string; version: number; status: string; issued_at: string | null; expires_at: string; snapshot: { total: string; currency: string; lines: QuoteLine[] } }[]; events: { id: string; from_status: string | null; to_status: string; reason_code: string; created_at: string; actor_type: string }[] };
type Counteroffer = { requested_decision: string; requested_items: { product_id: string; quantity: number; requested_discount_pct: string; currency: string; total: string; decision: { decision: string; reason_code: string; human_message: string } }[]; manager_review_available: boolean; approval_already_granted: boolean; alternative: { decision: string; explanation: string; items: { product_id: string; quantity: number; discount_pct: string; total: string | null; currency: string; policy_id: string; policy_version: string }[] } };

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
  const [activeFilter, setActiveFilter] = useState<"all" | "draft" | "formal">("all");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [emailDraft, setEmailDraft] = useState<EmailDraft | null>(null);
  const [lifecycle, setLifecycle] = useState<LifecycleHistory | null>(null);
  const [requestedDiscount, setRequestedDiscount] = useState("");
  const [requestedQuantity, setRequestedQuantity] = useState("");
  const [counteroffer, setCounteroffer] = useState<Counteroffer | null>(null);
  const [lifecycleNotice, setLifecycleNotice] = useState<{ message: string; tone: "info" | "success" | "error" } | null>(null);
  const [publicLink, setPublicLink] = useState("");
  const [lifecycleBusy, setLifecycleBusy] = useState(false);

  const loadQuotes = useCallback(async (query: string, filter: "all" | "draft" | "formal") => {
    setBusy(true);
    try {
      const params = new URLSearchParams({ search: query, limit: "50" });
      if (filter === "draft") params.set("status", "DRAFT");
      if (filter === "formal") params.set("status", "FORMAL");
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
    setEmailDraft(null);
    setLifecycle(null); setCounteroffer(null); setPublicLink("");
    if (!selectedId) { setDetail(null); return; }
    let cancelled = false;
    void fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { const payload = await response.json(); if (!response.ok) throw new Error(payload?.detail?.message ?? "无法加载报价详情"); return payload as QuoteDetail; })
      .then((payload) => { if (!cancelled) setDetail(payload); })
      .catch((error) => { if (!cancelled) { setDetail(null); setNotice(error instanceof Error ? error.message : "无法加载报价详情"); } });
    return () => { cancelled = true; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    void fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/history`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { const payload = await response.json(); if (!response.ok) throw new Error(payload?.detail?.message ?? "无法加载报价版本"); return payload as LifecycleHistory; })
      .then((payload) => { if (!cancelled) setLifecycle(payload); })
      .catch(() => { if (!cancelled) setLifecycle(null); });
    return () => { cancelled = true; };
  }, [selectedId, detail?.status]);

  useEffect(() => {
    if (!detail) return;
    setRequestedDiscount(detail.lines[0]?.discount_pct ?? detail.breakdown.discount_pct);
    setRequestedQuantity(detail.lines.length === 1 ? String(detail.lines[0].quantity) : "");
  }, [detail]);

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

  const exportQuotationPdf = async () => {
    if (!selectedId) return;
    try {
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/quotation.pdf?locale=${locale}`, { credentials: "include" });
      if (!response.ok) throw new Error(t("报价单 PDF 生成失败", "Could not generate the quotation PDF"));
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `dealguard-quotation-${selectedId}.pdf`; anchor.click(); URL.revokeObjectURL(url);
      setNotice(t("已生成报价草稿 PDF；该文件尚未正式发出。", "Draft quotation PDF generated; it has not been formally issued."));
    } catch (error) { setNotice(error instanceof Error ? error.message : t("报价单 PDF 生成失败", "Could not generate the quotation PDF")); }
  };

  const generateEmailDraft = async () => {
    if (!selectedId) return;
    try {
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/email-draft?locale=${locale}`, { credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("无法生成客户回复草稿", "Could not create a customer reply draft"));
      setEmailDraft(payload as EmailDraft);
      setNotice(t("客户回复草稿已生成，可复制后自行审核。", "Customer reply draft prepared; review it before copying."));
    } catch (error) { setNotice(error instanceof Error ? error.message : t("无法生成客户回复草稿", "Could not create a customer reply draft")); }
  };

  const copyEmailDraft = async () => {
    if (!emailDraft) return;
    try {
      await navigator.clipboard.writeText(`Subject: ${emailDraft.subject}\n\n${emailDraft.body}`);
      setNotice(t("客户回复草稿已复制。", "Customer reply draft copied."));
    } catch { setNotice(t("浏览器未授予剪贴板权限，请手动复制邮件内容。", "Clipboard access was denied. Copy the email text manually.")); }
  };

  const previewCounteroffer = async () => {
    if (!selectedId || !requestedDiscount) return;
    setLifecycleBusy(true); setCounteroffer(null); setLifecycleNotice(null);
    try {
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/counteroffer`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requested_discount_pct: requestedDiscount, ...(requestedQuantity ? { quantity: Number(requestedQuantity) } : {}) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("议价方案计算失败", "Could not calculate a counteroffer"));
      setCounteroffer(payload as Counteroffer);
      setLifecycleNotice({ message: t("已显示客户要求及当前政策下的直接合规方案；预览不会修改报价。", "The customer request and direct-limit alternative are shown below. Preview does not change the quote."), tone: "info" });
    } catch (error) { setLifecycleNotice({ message: error instanceof Error ? error.message : t("议价方案计算失败", "Could not calculate a counteroffer"), tone: "error" }); }
    finally { setLifecycleBusy(false); }
  };

  const saveAlternative = async () => {
    if (!selectedId || !counteroffer?.alternative.items.length) return;
    setLifecycleBusy(true);
    try {
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/revise`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines: counteroffer.alternative.items.map((item) => ({ product_id: item.product_id, quantity: item.quantity, requested_discount_pct: item.discount_pct })) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("保存修订失败", "Could not save the revision"));
      setNotice(payload.message ?? t("合规修订已保存", "Compliant revision saved")); setLifecycleNotice({ message: t("合规方案已保存为报价修订；请核对明细后发布。", "The compliant alternative is saved as a quote revision. Review the line items before issuing."), tone: "success" }); setCounteroffer(null);
      await Promise.all([loadQuotes(search, activeFilter), fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}`, { credentials: "include", cache: "no-store" }).then((r) => r.json()).then(setDetail)]);
    } catch (error) { setNotice(error instanceof Error ? error.message : t("保存修订失败", "Could not save the revision")); }
    finally { setLifecycleBusy(false); }
  };

  const requestManagerReview = async () => {
    if (!selectedId || !requestedDiscount) return;
    setLifecycleBusy(true);
    try {
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/counteroffer/approval`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requested_discount_pct: requestedDiscount, ...(requestedQuantity ? { quantity: Number(requestedQuantity) } : {}) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("提交审批失败", "Could not submit the approval request"));
      setNotice(t(`经理审批请求已提交（${payload.approval_id}）。`, `Manager review requested (${payload.approval_id}).`));
      setCounteroffer(null);
    } catch (error) { setNotice(error instanceof Error ? error.message : t("提交审批失败", "Could not submit the approval request")); }
    finally { setLifecycleBusy(false); }
  };

  const issueSelected = async () => {
    if (!selectedId || !detail) return;
    setLifecycleBusy(true); setLifecycleNotice(null);
    try {
      const quoteLines = detail.lines.length ? detail.lines : [{ product_id: detail.product_id, quantity: detail.breakdown.quantity, discount_pct: detail.breakdown.discount_pct }];
      const requestedDiscountChanged = quoteLines.some((line) => Number(line.discount_pct) !== Number(requestedDiscount));
      const requestedQuantityChanged = quoteLines.length === 1 && requestedQuantity !== "" && Number(quoteLines[0].quantity) !== Number(requestedQuantity);
      if (requestedDiscountChanged || requestedQuantityChanged) {
        const previewResponse = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/counteroffer`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ requested_discount_pct: requestedDiscount, ...(requestedQuantity ? { quantity: Number(requestedQuantity) } : {}) }),
        });
        const preview = await previewResponse.json() as Counteroffer;
        if (!previewResponse.ok) throw new Error(previewResponse.status === 403 ? t("只有报价负责人可以修订此报价。", "Only the quote owner can revise this quote.") : preview?.requested_items?.[0]?.decision?.human_message ?? t("无法验证客户要求的折扣。", "Could not validate the requested discount."));
        if (preview.requested_decision !== "ALLOW" && !(preview.requested_decision === "REVIEW" && preview.approval_already_granted)) {
          const reason = preview.requested_items.find((item) => item.decision.decision !== "ALLOW")?.decision.human_message;
          throw new Error(preview.requested_decision === "REVIEW"
            ? t("该折扣需要经理审批，请先提交审批后再发布。", "This discount requires manager approval. Submit it for review before issuing.")
            : reason ?? t("该折扣未通过当前政策校验。请预览并保存合规备选方案后再发布。", "This discount did not pass current policy checks. Preview and save a compliant alternative before issuing."));
        }
        const reviseResponse = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/revise`, {
          method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lines: preview.requested_items.map((item) => ({ product_id: item.product_id, quantity: item.quantity, requested_discount_pct: item.requested_discount_pct })) }),
        });
        const revisePayload = await reviseResponse.json();
        if (!reviseResponse.ok) throw new Error(revisePayload?.detail?.message ?? t("客户要求的折扣未能保存为报价修订。", "Could not save the requested discount as a quote revision."));
        const revisedQuote = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}`, { credentials: "include", cache: "no-store" }).then((response) => response.json());
        setDetail(revisedQuote);
        setNotice(t("客户要求的折扣已通过 Guard 校验并保存，正在发布更新后的明细。", "The requested discount passed Guard and was saved. Issuing the updated quote details."));
      }
      const response = await fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/issue`, { method: "POST", credentials: "include" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("发布报价失败", "Could not issue quote"));
      const link = `${window.location.origin}/public/quote/${encodeURIComponent(payload.access_token)}`;
      setPublicLink(link); setNotice(t("报价版本已发布。客户链接仅显示该报价，可复制后发送。", "Quote revision issued. Copy its scoped customer link to share it."));
      setLifecycleNotice({ message: t(`报价版本 v${payload.version} 已发布，明细已按当前报价保存。`, `Quote revision v${payload.version} issued with the saved line items.`), tone: "success" });
      await loadQuotes(search, activeFilter);
      const [quoteResponse, historyResponse] = await Promise.all([
        fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}`, { credentials: "include", cache: "no-store" }),
        fetch(`${API}/api/v1/quotes/${encodeURIComponent(selectedId)}/history`, { credentials: "include", cache: "no-store" }),
      ]);
      setDetail(await quoteResponse.json()); setLifecycle(await historyResponse.json());
    } catch (error) { const message = error instanceof Error ? error.message : t("发布报价失败", "Could not issue quote"); setNotice(message); setLifecycleNotice({ message, tone: "error" }); }
    finally { setLifecycleBusy(false); }
  };

  const copyPublicLink = async () => {
    if (!publicLink) return;
    try { await navigator.clipboard.writeText(publicLink); setNotice(t("客户确认链接已复制。", "Customer response link copied.")); }
    catch { setNotice(t("无法访问剪贴板，请手动复制链接。", "Clipboard access denied; copy the link manually.")); }
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
          <button type="button" aria-pressed={activeFilter === "formal"} onClick={() => setActiveFilter("formal")}>{t("正式报价", "Issued quotes")}</button>
        </div>
        <div className="dg-quote-layout">
          <section className="dg-operations-panel dg-quote-list" aria-label="报价列表">
            <div className="dg-quote-list-head"><strong>{t("报价记录", "Quote records")}</strong><div className="dg-quote-list-tools"><span>{busy ? t("正在更新…", "Updating…") : t(`共 ${total} 条`, `${total} total`)}</span><button type="button" className="dg-quote-list-refresh" disabled={busy} onClick={() => void loadQuotes(search, activeFilter)}>{t("刷新", "Refresh")}</button></div></div>
            <div className="dg-quote-table-wrap"><table className="dg-quote-table"><thead><tr><th>{t("报价编号", "Quote ID")}</th><th>{t("客户", "Customer")}</th><th>{t("状态", "Status")}</th><th>{t("总额", "Total")}</th><th>{t("政策版本", "Policy version")}</th><th>{t("创建时间", "Created")}</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.id} aria-selected={row.id === selectedId} onClick={() => setSelectedId(row.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedId(row.id); }} tabIndex={0}>
                <td><span className="dg-quote-id">{row.id.slice(0, 8)}</span><div className="dg-quote-subrow">{t(`${row.line_count} 个商品`, `${row.line_count} items`)}</div></td><td>{row.customer_name}<div className="dg-quote-subrow">{row.customer_id}</div></td><td><span className={`dg-quote-state dg-quote-state-${row.status.toLowerCase()}`}>{localizedStatus(row.status, locale)}</span></td><td className="dg-quote-amount">{money(row.total, row.currency)}</td><td>{row.policy_id}@{row.policy_version}</td><td>{timestamp(row.created_at, locale)}</td>
              </tr>)}</tbody></table>{!rows.length && <div className="dg-operations-empty">{busy ? t("正在加载报价…", "Loading quotes…") : t("没有匹配的报价。创建询盘并获准后，报价草稿会显示在这里。", "No matching quotes. Approved inquiries create quote drafts here.")}</div>}</div>
            <div className="dg-operations-count">{t(`显示最近 ${rows.length} 条 · 当前身份只能读取有权限的报价`, `Showing ${rows.length} recent quotes · Your account only sees quotes it can access`)}</div>
          </section>
          <aside className="dg-operations-panel dg-quote-detail" aria-label="报价详情">
            {!selected || !detail ? <div className="dg-operations-empty">{t("选择一条报价查看可追溯明细", "Select a quote to review its traceable details")}</div> : <>
              <div className="dg-quote-detail-top"><div><h2>{t("报价单", "Quote")} {selected.id.slice(0, 8)}</h2><p className="dg-quote-detail-sub"><span className="dg-status-draft">{localizedStatus(detail.status, locale)}</span> · {t("创建于", "Created")} {timestamp(detail.created_at, locale)}</p></div><div className="dg-quote-delivery-actions"><button className="dg-operations-secondary" type="button" onClick={() => void exportSelected()}>{t("导出 CSV", "Export CSV")}</button><button className="dg-operations-secondary" type="button" onClick={() => void exportQuotationPdf()}>{t("下载报价单 PDF", "Download quotation PDF")}</button></div></div>
              <div className="dg-detail-section"><h3>{t("报价信息", "Quote information")}</h3><dl className="dg-detail-meta"><div><dt>{t("客户编号", "Customer ID")}</dt><dd>{detail.customer_id}</dd></div><div><dt>{t("商品项目", "Items")}</dt><dd>{t(`${detail.lines.length || 1} 项`, `${detail.lines.length || 1} items`)}</dd></div><div><dt>{t("适用政策", "Applied policy")}</dt><dd>{detail.policy_id}@{detail.policy_version}</dd></div><div><dt>{t("币种", "Currency")}</dt><dd>{detail.breakdown.currency}</dd></div></dl></div>
              <div className="dg-detail-section dg-quote-lifecycle">
                <div className="dg-quote-lifecycle-head"><div><h3>{t("报价生命周期", "Quote lifecycle")}</h3><p>{t("修改会重新经过 Guard 校验并保存修订；发布后生成客户可查看的限时版本。", "Changes are rechecked by Guard and saved as a revision. Issuing creates a time-limited customer view.")}</p></div>{["DRAFT", "REVISED"].includes(detail.status) && <button className="dg-operations-primary" type="button" disabled={lifecycleBusy} onClick={() => void issueSelected()}>{lifecycleBusy ? t("处理中…", "Working…") : t("发布报价版本", "Issue quote revision")}</button>}</div>
                <div className="dg-counteroffer-form"><label>{t("客户要求折扣 (%)", "Requested discount (%)")}<input aria-label={t("客户要求折扣", "Requested discount")} type="number" min="0" max="100" step="0.01" value={requestedDiscount} onChange={(event) => setRequestedDiscount(event.target.value)} /></label>{detail.lines.length === 1 && <label>{t("报价数量", "Quote quantity")}<input aria-label={t("报价数量", "Quote quantity")} type="number" min="1" value={requestedQuantity} onChange={(event) => setRequestedQuantity(event.target.value)} /></label>}<button className="dg-operations-secondary" type="button" disabled={lifecycleBusy} onClick={() => void previewCounteroffer()}>{t("预览合规方案", "Preview compliant alternative")}</button></div>
                <p className="dg-lifecycle-help">{t("输入与当前明细相同时，发布当前报价；如果折扣或数量有变化，系统会先校验并保存修订。", "If the values match the current line items, publishing issues this quote as saved. Changed discounts or quantities are checked and saved as a revision first.")}</p>
                {lifecycleNotice && <p className={`dg-lifecycle-notice dg-lifecycle-notice-${lifecycleNotice.tone}`} role="status">{lifecycleNotice.message}</p>}
                {counteroffer && <div className="dg-counteroffer-result" role="status">
                  <div><strong>{t("方案对比", "Offer comparison")}</strong><p>{t("预览只计算金额和 Guard 决策，不会修改报价。", "Preview calculates totals and Guard decisions without changing the saved quote.")}</p></div>
                  <section className="dg-counteroffer-group"><h4>{t("客户要求", "Customer request")}</h4>{counteroffer.requested_items.map((item) => <div className="dg-counteroffer-line" key={item.product_id}><span>{item.product_id} · {item.quantity.toLocaleString()} {t("件", "units")} · {item.requested_discount_pct}%<small>{localizedDecision(item.decision.decision, locale)}{item.decision.reason_code === "MANAGER_APPROVAL_REQUIRED" ? ` · ${counteroffer.approval_already_granted ? t("经理已批准", "manager approved") : t("需经理审批", "manager approval required")}` : ""}</small></span><strong>{money(item.total, item.currency)}</strong></div>)}</section>
                  {counteroffer.approval_already_granted && <p className="dg-lifecycle-notice dg-lifecycle-notice-success" role="status">{t("此折扣已获经理审批，可以发布当前报价。", "This discount has manager approval and can be issued.")}</p>}
                  <section className="dg-counteroffer-group"><h4>{t("当前政策下可直接报价的方案", "Direct-limit alternative under the active policy")}</h4>{counteroffer.alternative.items.map((item) => <div className="dg-counteroffer-line" key={item.product_id}><span>{item.product_id} · {item.quantity.toLocaleString()} {t("件", "units")} · {item.discount_pct}%</span><strong>{item.total ? money(item.total, item.currency) : t("不可用", "Unavailable")}</strong></div>)}</section>
                  {counteroffer.alternative.decision === "ALLOW" && <button className="dg-operations-secondary" type="button" disabled={lifecycleBusy} onClick={() => void saveAlternative()}>{t("保存合规方案为报价修订", "Save compliant alternative as revision")}</button>}{counteroffer.manager_review_available && !counteroffer.approval_already_granted && <button className="dg-operations-primary" type="button" disabled={lifecycleBusy} onClick={() => void requestManagerReview()}>{t("提交客户要求的折扣审批", "Submit requested discount for approval")}</button>}
                </div>}
                {publicLink && <div className="dg-quote-share"><label>{t("客户专属确认链接", "Scoped customer response link")}<input readOnly value={publicLink} /></label><button className="dg-operations-secondary" type="button" onClick={() => void copyPublicLink()}>{t("复制链接", "Copy link")}</button></div>}
                {lifecycle?.revisions.length ? <div className="dg-quote-revisions"><h4>{t("版本记录", "Revision history")}</h4>{lifecycle.revisions.map((revision) => <article key={revision.id}><div><strong>v{revision.version}</strong><span className={`dg-quote-state dg-quote-state-${revision.status.toLowerCase()}`}>{localizedStatus(revision.status, locale)}</span></div><p>{money(revision.snapshot.total, revision.snapshot.currency)} · {timestamp(revision.issued_at ?? revision.expires_at, locale)}</p></article>)}</div> : <p className="dg-quote-lifecycle-empty">{t("尚无已发布版本。", "No issued revisions yet.")}</p>}
                {lifecycle?.events.length ? <details className="dg-quote-events"><summary>{t("状态事件", "Status events")} · {lifecycle.events.length}</summary><ol>{lifecycle.events.map((event) => <li key={event.id}><strong>{event.from_status ? `${localizedStatus(event.from_status, locale)} → ` : ""}{localizedStatus(event.to_status, locale)}</strong><span>{localizedStatus(event.reason_code, locale)}</span><time>{timestamp(event.created_at, locale)}</time></li>)}</ol></details> : null}
              </div>
              <div className="dg-detail-section"><h3>{t("报价明细", "Line items")}</h3><div className="dg-quote-table-wrap"><table className="dg-quote-line-table"><thead><tr><th>{t("商品", "Product")}</th><th>{t("单价", "Unit price")}</th><th>{t("数量", "Quantity")}</th><th>{t("折扣", "Discount")}</th><th>{t("行金额", "Line total")}</th></tr></thead><tbody>
                {(detail.lines.length ? detail.lines : [{ product_id: detail.product_id, product_name: detail.product_id, currency: detail.breakdown.currency, quantity: detail.breakdown.quantity, unit_price: detail.breakdown.unit_price, discount_pct: detail.breakdown.discount_pct, subtotal: detail.breakdown.subtotal, discount_amount: detail.breakdown.discount_amount, total: detail.breakdown.total, policy_id: detail.policy_id, policy_version: detail.policy_version, policy_source: "" }]).map((line, index) => <tr key={`${line.product_id}-${index}`}><td><span className="dg-quote-product-id">{line.product_id}</span><div className="dg-quote-subrow">{line.product_name}</div></td><td>{money(line.unit_price, line.currency)}</td><td>{line.quantity.toLocaleString()}</td><td><span className="dg-quote-discount">{line.discount_pct}%</span><div className="dg-quote-subrow">−{money(line.discount_amount, line.currency)}</div></td><td className="dg-quote-line-total">{money(line.total, line.currency)}</td></tr>)}
              </tbody></table></div><div className="dg-quote-totals"><div><span>{t("折前金额", "Subtotal")}</span><strong>{money(detail.breakdown.subtotal, detail.breakdown.currency)}</strong></div><div><span>{t("折扣金额", "Discount")}</span><strong>−{money(detail.breakdown.discount_amount, detail.breakdown.currency)}</strong></div><div className="dg-quote-total"><span>{t("报价总额", "Quote total")}</span><strong>{money(detail.breakdown.total, detail.breakdown.currency)}</strong></div></div></div>
              <div className="dg-detail-section dg-quote-email"><div className="dg-quote-email-head"><div><h3>{t("客户回复草稿", "Customer reply draft")}</h3><p>{t("金额和明细取自报价数据；发送前请先审核。", "Amounts and items come from the quote record; review before sending.")}</p></div><button className="dg-operations-secondary" type="button" onClick={() => void generateEmailDraft()}>{t("生成邮件草稿", "Prepare email draft")}</button></div>{emailDraft?.quote_id === detail.id && <div className="dg-quote-email-body"><label>{t("邮件主题", "Subject")}<input readOnly value={emailDraft.subject} /></label><label>{t("邮件内容", "Message")}<textarea readOnly rows={8} value={emailDraft.body} /></label><button className="dg-operations-secondary" type="button" onClick={() => void copyEmailDraft()}>{t("复制邮件草稿", "Copy email draft")}</button></div>}</div>
              {evidenceGroups.map((group, index) => { const line = group[0]; const translatedCitation = locale === "zh" ? translatePolicyCitation(line.policy_id, line.policy_version, line.citation) : null; const products = [...new Map(group.map((item) => [item.product_id, item])).values()]; return <div className="dg-detail-section" key={`evidence-${line.policy_id}-${line.policy_version}-${line.citation_ref ?? index}`}><h3>{t("政策依据", "Policy evidence")} · {line.policy_id}@{line.policy_version}</h3><div className="dg-evidence-box"><strong>{line.citation_ref || t("未保存条款引用", "No clause reference saved")}</strong>{line.chunk_id && <code>{t("政策分块编号", "Policy chunk ID")}: {line.chunk_id}</code>}<div className="dg-evidence-products"><span>{t("适用商品", "Applies to")}</span>{products.map((item) => <span className="dg-evidence-product" key={item.product_id}>{item.product_id} · {item.product_name}</span>)}</div>{line.citation ? <>{translatedCitation ? <><p>{translatedCitation}</p>{locale === "zh" && <details className="dg-policy-original"><summary>查看政策英文原文</summary><p>{line.citation}</p></details>}</> : <><p>{line.citation}</p>{locale === "zh" && <p className="dg-policy-translation-note">该政策暂无中文译文，以下保留原文，避免改变政策含义。</p>}</>}</> : <p>{t("该报价未包含可展示的精确条款引用。", "This quote has no exact clause citation to display.")}</p>}</div></div>; })}
              {detail.lines.map((line, index) => line.guard_evaluation?.checks?.length ? <div className="dg-detail-section" key={`guard-${line.product_id}-${index}`}><details className="dg-guard-details"><summary>{t(`查看 Guard 检查 · ${line.product_id}（${line.guard_evaluation.checks.length}）`, `View Guard checks · ${line.product_id} (${line.guard_evaluation.checks.length})`)}</summary><ul>{line.guard_evaluation.checks.map((check, checkIndex) => <li key={checkIndex}><span>{translateGuardCheck(check.check, locale)}</span><strong>{translateGuardResult(check.result, locale)}</strong></li>)}</ul></details></div> : null)}
            </>}
          </aside>
        </div>
      </main>
    </div>
  );
}
