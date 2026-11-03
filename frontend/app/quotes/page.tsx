"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { WorkspaceNav } from "../../components/WorkspaceNav";
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

function timestamp(value: string) {
  return new Date(value).toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" });
}

export default function QuotesPage() {
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
        <header className="dg-operations-head"><div><h1>报价中心</h1><p>查找报价草稿，复核明细、适用政策和 Guard 检查，再导出可追溯记录。</p></div><div className="dg-operations-actions"><Link className="dg-operations-primary" href="/">新建询盘</Link></div></header>
        {notice && <div className="dg-operations-notice" role="status">{notice} {notice.startsWith("请先登录") && <Link href="/">返回登录</Link>}</div>}
        <div className="dg-operations-toolbar"><input className="dg-operations-search" aria-label="搜索报价" placeholder="搜索报价编号、客户或政策编号" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
        <div className="dg-operations-tabs" role="group" aria-label="报价状态筛选">
          <button type="button" aria-pressed={activeFilter === "all"} onClick={() => setActiveFilter("all")}>全部报价</button>
          <button type="button" aria-pressed={activeFilter === "draft"} onClick={() => setActiveFilter("draft")}>报价草稿</button>
        </div>
        <div className="dg-quote-layout">
          <section className="dg-operations-panel dg-quote-list" aria-label="报价列表">
            <div className="dg-quote-list-head"><strong>报价记录</strong><span>{busy ? "正在更新…" : `共 ${total} 条`}</span></div>
            <div className="dg-quote-table-wrap"><table className="dg-quote-table"><thead><tr><th>报价编号</th><th>客户</th><th>总额</th><th>政策版本</th><th>创建时间</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.id} aria-selected={row.id === selectedId} onClick={() => setSelectedId(row.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedId(row.id); }} tabIndex={0}>
                <td><span className="dg-quote-id">{row.id.slice(0, 8)}</span><div className="dg-quote-subrow">{row.line_count} 个商品</div></td><td>{row.customer_name}<div className="dg-quote-subrow">{row.customer_id}</div></td><td className="dg-quote-amount">{money(row.total, row.currency)}</td><td>{row.policy_id}@{row.policy_version}</td><td>{timestamp(row.created_at)}</td>
              </tr>)}</tbody></table>{!rows.length && <div className="dg-operations-empty">{busy ? "正在加载报价…" : "没有匹配的报价。创建询盘并获准后，报价草稿会显示在这里。"}</div>}</div>
            <div className="dg-operations-count">显示最近 {rows.length} 条 · 当前身份只能读取有权限的报价</div>
          </section>
          <aside className="dg-operations-panel dg-quote-detail" aria-label="报价详情">
            {!selected || !detail ? <div className="dg-operations-empty">选择一条报价查看可追溯明细</div> : <>
              <div className="dg-quote-detail-top"><div><h2>报价单 {selected.id.slice(0, 8)}</h2><p className="dg-quote-detail-sub"><span className="dg-status-draft">{detail.status === "DRAFT" ? "报价草稿" : detail.status}</span> · 创建于 {timestamp(detail.created_at)}</p></div><button className="dg-operations-secondary" type="button" onClick={() => void exportSelected()}>导出 CSV</button></div>
              <div className="dg-detail-section"><h3>报价信息</h3><dl className="dg-detail-meta"><div><dt>客户编号</dt><dd>{detail.customer_id}</dd></div><div><dt>商品项目</dt><dd>{detail.lines.length || 1} 项</dd></div><div><dt>适用政策</dt><dd>{detail.policy_id}@{detail.policy_version}</dd></div><div><dt>币种</dt><dd>{detail.breakdown.currency}</dd></div></dl></div>
              <div className="dg-detail-section"><h3>报价明细</h3><div className="dg-quote-table-wrap"><table className="dg-quote-lines"><thead><tr><th>商品</th><th>数量</th><th>折扣</th><th>金额</th></tr></thead><tbody>
                {(detail.lines.length ? detail.lines : [{ product_id: detail.product_id, product_name: detail.product_id, currency: detail.breakdown.currency, quantity: detail.breakdown.quantity, unit_price: detail.breakdown.unit_price, discount_pct: detail.breakdown.discount_pct, subtotal: detail.breakdown.subtotal, discount_amount: detail.breakdown.discount_amount, total: detail.breakdown.total, policy_id: detail.policy_id, policy_version: detail.policy_version, policy_source: "" }]).map((line, index) => <tr key={`${line.product_id}-${index}`}><td><strong>{line.product_id}</strong><div className="dg-quote-subrow">{line.product_name}</div></td><td>{line.quantity}</td><td>{line.discount_pct}%</td><td>{money(line.total, line.currency)}</td></tr>)}
              </tbody></table></div><div className="dg-quote-totals"><div><span>折前金额</span><strong>{money(detail.breakdown.subtotal, detail.breakdown.currency)}</strong></div><div><span>折扣金额</span><strong>−{money(detail.breakdown.discount_amount, detail.breakdown.currency)}</strong></div><div className="dg-quote-total"><span>报价总额</span><strong>{money(detail.breakdown.total, detail.breakdown.currency)}</strong></div></div></div>
              {detail.lines.map((line, index) => <div className="dg-detail-section" key={`evidence-${line.product_id}-${index}`}><h3>政策依据 · {line.policy_id}@{line.policy_version}</h3><div className="dg-evidence-box"><strong>{line.citation_ref || "未保存条款引用"}</strong>{line.chunk_id && <code>{line.chunk_id}</code>}<p>{line.citation || "该报价未包含可展示的精确条款引用。"}</p></div>{line.guard_evaluation?.checks?.length ? <details className="dg-guard-details"><summary>查看 Guard 检查（{line.guard_evaluation.checks.length}）</summary><ul>{line.guard_evaluation.checks.map((check, checkIndex) => <li key={checkIndex}><span>{check.check}</span><strong>{check.result}</strong></li>)}</ul></details> : null}</div>)}
            </>}
          </aside>
        </div>
      </main>
    </div>
  );
}
