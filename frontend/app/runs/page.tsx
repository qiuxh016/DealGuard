"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import "../history.css";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { localizedDecision, localizedDecisionMessage, localizedReason, localizedStatus, localizedTraceMessage, useLanguage } from "../../components/LanguageProvider";
import { RunAnalytics } from "../../components/RunAnalytics";

type RunSummary = {
  run_id: string; actor_id: string; status: string; execution_mode: string; decision: string | null;
  reason_code: string | null; policy_id: string | null; policy_version: string | null;
  quote_id: string | null; quote_total: string | null; created_at: string; duration_ms: number | null;
};
type TraceEvent = { step: string; status: string; message: string; timestamp?: string; details?: Record<string, unknown> };
type RunDetail = {
  run_id: string; status: string; execution_mode: string; intent?: Record<string, unknown> | null;
  decision?: Record<string, unknown> | null;
  quote?: { id?: string; status?: string; breakdown?: { total?: string; currency?: string } } | null;
  policy_evidence: { policy_id?: string; version?: string; title?: string; citation?: string; citation_ref?: string; source?: string }[];
  trace: TraceEvent[];
};

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const money = (value: string | null) => value === null ? "—" : `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;
const traceLabel = (value: string, locale: "zh" | "en") => {
  const labels: Record<string, [string, string]> = {
    parse_inquiry: ["解析询盘", "Parse inquiry"], parse_intent: ["解析询盘", "Parse inquiry"], action_planner: ["规划操作", "Plan action"], validate_identity: ["校验身份", "Validate identity"], retrieve_policy: ["检索政策", "Retrieve policy"], guard_check: ["Guard 授权校验", "Guard authorization"], guard_authorize: ["Guard 授权校验", "Guard authorization"], calculate_quote: ["计算报价", "Calculate quote"], save_quote: ["保存报价草稿", "Save quote draft"], create_quote: ["创建报价草稿", "Create quote draft"], complete: ["完成", "Complete"],
  };
  return labels[value]?.[locale === "zh" ? 0 : 1] ?? value.replaceAll("_", " ");
};
const traceValue = (value: unknown): string => typeof value === "string" ? value : JSON.stringify(value) ?? String(value);

export default function RunsPage() {
  const { locale, t } = useLanguage();
  const [actor, setActor] = useState("");
  const [role, setRole] = useState("");
  const [rows, setRows] = useState<RunSummary[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [loadingRunId, setLoadingRunId] = useState("");
  const [traceError, setTraceError] = useState("");
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
      params.set("limit", "100");
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
    void fetch(`${API}/api/v1/runs?limit=100`, { headers: headers(), credentials: "include", cache: "no-store" }).then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取运行记录");
      setRows(payload.items);
    }).catch((error) => setNotice(error instanceof Error ? error.message : "无法读取运行记录"));
  }, [actor, headers]);

  useEffect(() => {
    if (!selectedRunId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedRunId("");
        setDetail(null);
        setTraceError("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedRunId]);

  async function openRun(runId: string) {
    setNotice("");
    setSelectedRunId(runId);
    setDetail(null);
    setTraceError("");
    setLoadingRunId(runId);
    try {
      const response = await fetch(`${API}/api/v1/runs/${runId}`, { headers: headers(), credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取运行详情");
      setDetail(payload);
    } catch (error) { setTraceError(error instanceof Error ? error.message : "无法读取运行详情"); }
    finally { setLoadingRunId(""); }
  }

  function closeTrace() {
    setSelectedRunId("");
    setDetail(null);
    setTraceError("");
  }

  return <><WorkspaceNav role={role} /><main className="dg-history"><div className="dg-history-main">
    <header className="dg-history-head"><div><h1>{t("运行记录", "Run history")}</h1><p>{t("查看受当前身份权限约束的决策、政策版本和 Trace。", "Review decisions, policy versions, and traces available to your account.")}</p></div></header>
    {rows.length > 0 && <RunAnalytics rows={rows} />}
    <section className="dg-history-panel" style={{ marginTop: 12 }}>
      <form className="dg-history-filters" onSubmit={(event) => void loadRuns(event)}>
        <label>{t("决策", "Decision")}<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">{t("全部", "All")}</option>{["ALLOW", "REVIEW", "DENY", "NEEDS_INPUT"].map((value) => <option key={value} value={value}>{localizedDecision(value, locale)}</option>)}</select></label>
        <label>{t("运行状态", "Run status")}<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">{t("全部", "All")}</option>{["completed", "needs_input", "failed", "running"].map((value) => <option key={value} value={value}>{localizedStatus(value, locale)}</option>)}</select></label>
        <label>{t("政策版本", "Policy version")}<input placeholder={t("例如 v2", "e.g. v2")} value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)} /></label>
        <label>{t("开始时间", "From")}<input type="datetime-local" value={createdFrom} onChange={(event) => setCreatedFrom(event.target.value)} /></label>
        <label>{t("结束时间", "To")}<input type="datetime-local" value={createdTo} onChange={(event) => setCreatedTo(event.target.value)} /></label>
        <button className="dg-history-button" disabled={busy}>{busy ? t("读取中…", "Loading…") : t("筛选", "Filter")}</button>
      </form>
      {notice && <p className="dg-history-notice" role="status">{notice}</p>}
      <div className="dg-history-table-wrap"><table className="dg-history-table"><thead><tr><th>{t("时间", "Time")}</th><th>{t("运行 / 身份", "Run / identity")}</th><th>{t("决策", "Decision")}</th><th>{t("政策", "Policy")}</th><th>{t("报价", "Quote")}</th><th>{t("耗时", "Duration")}</th><th /></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.run_id}><td>{new Date(row.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}</td><td><code>{row.run_id.slice(0, 8)}</code><br />{row.actor_id}</td><td>{localizedDecision(row.decision, locale) || localizedStatus(row.status, locale)}<br /><small>{localizedReason(row.reason_code, locale)}</small></td><td>{row.policy_id ? `${row.policy_id}@${row.policy_version}` : "—"}</td><td>{money(row.quote_total)}</td><td>{row.duration_ms === null ? "—" : `${row.duration_ms} ms`}</td><td><button className="dg-history-button" onClick={() => void openRun(row.run_id)} disabled={loadingRunId === row.run_id}>{loadingRunId === row.run_id ? t("读取中…", "Loading…") : t("查看 Trace", "View trace")}</button></td></tr>)}</tbody>
      </table>{rows.length === 0 && <div className="dg-history-empty">{t("没有匹配的运行记录", "No matching runs")}</div>}</div>
    </section>
  </div></main>
    {selectedRunId && <div className="dg-trace-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) closeTrace(); }}>
      <section className="dg-trace-dialog" role="dialog" aria-modal="true" aria-labelledby="dg-trace-title">
        <header><div><h2 id="dg-trace-title">{t("运行 Trace", "Run trace")}</h2><code>{selectedRunId}</code></div><button type="button" onClick={closeTrace} aria-label={t("关闭运行 Trace", "Close run trace")}>{t("关闭", "Close")}</button></header>
        {loadingRunId === selectedRunId ? <p className="dg-trace-state" role="status">{t("正在读取运行详情…", "Loading run details…")}</p>
          : traceError ? <p className="dg-trace-error" role="alert">{traceError}</p>
          : detail && <div className="dg-trace-content">
            <section className="dg-trace-overview">
              <article><span>{t("运行状态", "Run status")}</span><strong className={`dg-trace-status dg-trace-status-${detail.status}`}>{localizedStatus(detail.status, locale)}</strong></article>
              <article><span>{t("解析模式", "Parsing mode")}</span><strong>{detail.execution_mode === "llm" ? t("模型解析", "LLM") : t("规则解析", "Rules")}</strong></article>
              <article><span>{t("执行步骤", "Trace events")}</span><strong>{detail.trace.length}</strong></article>
              <article><span>{t("报价结果", "Quote result")}</span><strong>{detail.quote?.breakdown?.total ? money(detail.quote.breakdown.total) : t("未创建", "None")}</strong></article>
            </section>
            <div className="dg-trace-summary-grid">
              {detail.decision && <section className="dg-trace-summary-card"><h3>{t("Guard 决策", "Guard decision")}</h3><strong>{localizedDecision(String(detail.decision.decision ?? ""), locale)}</strong><code>{localizedReason(String(detail.decision.reason_code ?? ""), locale)}</code>{detail.decision.human_message != null && <p>{localizedDecisionMessage(String(detail.decision.reason_code ?? ""), String(detail.decision.human_message), locale)}</p>}</section>}
              {detail.intent && <section className="dg-trace-summary-card"><h3>{t("识别意图", "Parsed intent")}</h3><dl>{Object.entries(detail.intent).filter(([key, value]) => key !== "customer_message" && value !== null && value !== "" && !(Array.isArray(value) && value.length === 0)).slice(0, 8).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{traceValue(value)}</dd></div>)}</dl></section>}
            </div>
            {detail.policy_evidence.length > 0 && <section className="dg-trace-evidence"><h3>{t("政策依据", "Policy evidence")}</h3>{detail.policy_evidence.map((item, index) => <article key={`${item.policy_id}-${item.version}-${index}`}><div><strong>{item.title || `${item.policy_id}@${item.version}`}</strong><code>{item.citation_ref || item.source || ""}</code></div><p>{item.citation}</p></article>)}</section>}
            <section className="dg-trace-timeline"><div className="dg-trace-section-heading"><h3>{t("执行轨迹", "Execution timeline")}</h3><span>{t("点击步骤查看事件详情", "Expand a step to inspect event data")}</span></div>
              {detail.trace.length ? <ol>{detail.trace.map((event, index) => <li key={`${event.step}-${index}`} className={`dg-trace-event dg-trace-event-${event.status.toLowerCase()}`}><span className="dg-trace-step-marker">{index + 1}</span><details><summary><span><strong>{traceLabel(event.step, locale)}</strong><small>{localizedDecisionMessage(typeof event.details?.reason_code === "string" ? event.details.reason_code : null, localizedTraceMessage(event.message, locale), locale)}</small></span><time>{event.timestamp ? new Date(event.timestamp).toLocaleTimeString(locale === "zh" ? "zh-CN" : "en-US") : ""}</time></summary>{event.details && Object.keys(event.details).length > 0 && <pre>{JSON.stringify(event.details, null, 2)}</pre>}</details></li>)}</ol> : <p className="dg-trace-empty">{t("此运行没有保存步骤事件。", "No trace events were saved for this run.")}</p>}
            </section>
            <details className="dg-trace-raw"><summary>{t("查看完整运行数据 JSON", "View complete run JSON")}</summary><pre>{JSON.stringify(detail, null, 2)}</pre></details>
          </div>}
      </section>
    </div>}
  </>;
}
