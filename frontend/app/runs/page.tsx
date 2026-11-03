"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import "../history.css";

type RunSummary = {
  run_id: string; actor_id: string; status: string; execution_mode: string; decision: string | null;
  reason_code: string | null; policy_id: string | null; policy_version: string | null;
  quote_id: string | null; quote_total: string | null; created_at: string; duration_ms: number | null;
};
type RunDetail = { run_id: string; status: string; execution_mode: string; intent?: unknown; decision?: unknown; quote?: unknown; policy_evidence: unknown[]; trace: unknown[] };

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const money = (value: string | null) => value === null ? "—" : `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;

export default function RunsPage() {
  const [actor, setActor] = useState("");
  const [role, setRole] = useState("");
  const [rows, setRows] = useState<RunSummary[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [decision, setDecision] = useState("");
  const [status, setStatus] = useState("");
  const [policyVersion, setPolicyVersion] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const headers = useCallback((): Record<string, string> => ({}), []);
  const loadRuns = useCallback(async (event?: FormEvent) => {
    event?.preventDefault(); setBusy(true); setNotice("");
    const params = new URLSearchParams();
    if (decision) params.set("decision", decision);
    if (status) params.set("status", status);
    if (policyVersion.trim()) params.set("policy_version", policyVersion.trim());
    if (createdFrom) params.set("created_from", new Date(createdFrom).toISOString());
    if (createdTo) params.set("created_to", new Date(createdTo).toISOString());
    try {
      const response = await fetch(`${API}/api/v1/runs?${params}`, { headers: headers(), credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取运行记录");
      setRows(payload.items);
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法读取运行记录"); }
    finally { setBusy(false); }
  }, [createdFrom, createdTo, decision, headers, policyVersion, status]);

  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" }).then(async (response) => {
      if (!response.ok) throw new Error("请先登录后查看运行记录");
      const user = await response.json(); setActor(user.actor_id); setRole(user.role);
    }).catch((error) => setNotice(error instanceof Error ? error.message : "登录状态无效"));
  }, []);
  useEffect(() => {
    if (!actor) return;
    void fetch(`${API}/api/v1/runs`, { headers: headers(), credentials: "include", cache: "no-store" }).then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取运行记录");
      setRows(payload.items);
    }).catch((error) => setNotice(error instanceof Error ? error.message : "无法读取运行记录"));
  }, [actor, headers]);

  async function openRun(runId: string) {
    setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/runs/${runId}`, { headers: headers(), credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取运行详情");
      setDetail(payload);
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法读取运行详情"); }
  }

  return <main className="dg-history"><div className="dg-history-main">
    <header className="dg-history-head"><div><h1>运行记录</h1><p>查看受当前身份权限约束的决策、政策版本和 Trace。</p></div><nav className="dg-history-nav"><Link href="/">工作台</Link>{(role === "manager" || role === "admin") && <Link href="/evaluation">检索评测</Link>}{role === "admin" && <Link href="/audit">政策审计</Link>}</nav></header>
    <section className="dg-history-panel" style={{ marginTop: 12 }}>
      <form className="dg-history-filters" onSubmit={(event) => void loadRuns(event)}>
        <label>决策<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">全部</option>{["ALLOW", "REVIEW", "DENY", "NEEDS_INPUT"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>运行状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option>{["completed", "needs_input", "failed", "running"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>政策版本<input placeholder="例如 v2" value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)} /></label>
        <label>开始时间<input type="datetime-local" value={createdFrom} onChange={(event) => setCreatedFrom(event.target.value)} /></label>
        <label>结束时间<input type="datetime-local" value={createdTo} onChange={(event) => setCreatedTo(event.target.value)} /></label>
        <button className="dg-history-button" disabled={busy}>{busy ? "读取中…" : "筛选"}</button>
      </form>
      {notice && <p className="dg-history-notice" role="status">{notice}</p>}
      <div className="dg-history-table-wrap"><table className="dg-history-table"><thead><tr><th>时间</th><th>运行 / 身份</th><th>决策</th><th>政策</th><th>报价</th><th>耗时</th><th /></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.run_id}><td>{new Date(row.created_at).toLocaleString()}</td><td><code>{row.run_id.slice(0, 8)}</code><br />{row.actor_id}</td><td>{row.decision ?? row.status}<br /><small>{row.reason_code}</small></td><td>{row.policy_id ? `${row.policy_id}@${row.policy_version}` : "—"}</td><td>{money(row.quote_total)}</td><td>{row.duration_ms === null ? "—" : `${row.duration_ms} ms`}</td><td><button className="dg-history-button" onClick={() => void openRun(row.run_id)}>查看 Trace</button></td></tr>)}</tbody>
      </table>{rows.length === 0 && <div className="dg-history-empty">没有匹配的运行记录</div>}</div>
      {detail && <div className="dg-history-detail"><h2>运行详情 · {detail.run_id}</h2><pre>{JSON.stringify({ status: detail.status, mode: detail.execution_mode, intent: detail.intent, decision: detail.decision, quote: detail.quote, policy_evidence: detail.policy_evidence, trace: detail.trace }, null, 2)}</pre></div>}
    </section>
  </div></main>;
}
