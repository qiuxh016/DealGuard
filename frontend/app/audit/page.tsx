"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import "../history.css";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { useLanguage } from "../../components/LanguageProvider";

type AuditEvent = { id: string; actor_id: string; action: string; outcome: string; reason_code: string | null; policy_id: string | null; version: string | null; created_at: string };
const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function AuditPage() {
  const { locale, t } = useLanguage();
  const [actor, setActor] = useState("");
  const [rows, setRows] = useState<AuditEvent[]>([]);
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const [policyId, setPolicyId] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const headers = useCallback((): Record<string, string> => ({}), []);
  const loadAudit = useCallback(async (event?: FormEvent) => {
    event?.preventDefault(); setBusy(true); setNotice("");
    const params = new URLSearchParams();
    if (action) params.set("action", action);
    if (outcome) params.set("outcome", outcome);
    if (policyId.trim()) params.set("policy_id", policyId.trim());
    if (createdFrom) params.set("created_from", new Date(createdFrom).toISOString());
    if (createdTo) params.set("created_to", new Date(createdTo).toISOString());
    try {
      const response = await fetch(`${API}/api/v1/audit?${params}`, { headers: headers(), credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取审计记录");
      setRows(payload.items);
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法读取审计记录"); }
    finally { setBusy(false); }
  }, [action, createdFrom, createdTo, headers, outcome, policyId]);

  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" }).then(async (response) => {
      if (!response.ok) throw new Error("请先登录后查看审计记录");
      const user = await response.json(); setActor(user.actor_id);
    }).catch((error) => setNotice(error instanceof Error ? error.message : "登录状态无效"));
  }, []);
  useEffect(() => {
    if (!actor) return;
    void fetch(`${API}/api/v1/audit`, { headers: headers(), credentials: "include", cache: "no-store" }).then(async (response) => {
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取审计记录");
      setRows(payload.items);
    }).catch((error) => setNotice(error instanceof Error ? error.message : "无法读取审计记录"));
  }, [actor, headers]);

  return <><WorkspaceNav /><main className="dg-history"><div className="dg-history-main">
    <header className="dg-history-head"><div><h1>{t("政策管理审计", "Policy audit")}</h1><p>{t("记录政策草稿上传、规则编辑、激活及拒绝事件，不展示政策正文。", "Review policy draft uploads, rule edits, activations, and denied actions. Policy text is not shown here.")}</p></div></header>
    <section className="dg-history-panel" style={{ marginTop: 12 }}>
      <form className="dg-history-filters" onSubmit={(event) => void loadAudit(event)}>
        <label>{t("动作", "Action")}<input placeholder={t("全部动作", "All actions")} value={action} onChange={(event) => setAction(event.target.value)} /></label>
        <label>{t("结果", "Outcome")}<select value={outcome} onChange={(event) => setOutcome(event.target.value)}><option value="">{t("全部", "All")}</option><option value="SUCCESS">{t("成功", "Success")}</option><option value="DENIED">{t("拒绝", "Denied")}</option></select></label>
        <label>{t("政策编号", "Policy ID")}<input placeholder={t("例如 DISCOUNT-02", "e.g. DISCOUNT-02")} value={policyId} onChange={(event) => setPolicyId(event.target.value)} /></label>
        <label>{t("开始时间", "From")}<input type="datetime-local" value={createdFrom} onChange={(event) => setCreatedFrom(event.target.value)} /></label>
        <label>{t("结束时间", "To")}<input type="datetime-local" value={createdTo} onChange={(event) => setCreatedTo(event.target.value)} /></label>
        <button className="dg-history-button" disabled={busy}>{busy ? t("读取中…", "Loading…") : t("筛选", "Filter")}</button>
      </form>
      {notice && <p className="dg-history-notice" role="status">{notice}</p>}
      <div className="dg-history-table-wrap"><table className="dg-history-table"><thead><tr><th>{t("时间", "Time")}</th><th>{t("操作者", "Actor")}</th><th>{t("动作", "Action")}</th><th>{t("结果", "Outcome")}</th><th>{t("原因码", "Reason code")}</th><th>{t("政策版本", "Policy version")}</th></tr></thead><tbody>
        {rows.map((row) => <tr key={row.id}><td>{new Date(row.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}</td><td>{row.actor_id}</td><td>{row.action}</td><td>{row.outcome === "SUCCESS" ? t("成功", "Success") : row.outcome === "DENIED" ? t("拒绝", "Denied") : row.outcome}</td><td>{row.reason_code ?? "—"}</td><td>{row.policy_id ? `${row.policy_id}@${row.version ?? "—"}` : "—"}</td></tr>)}
      </tbody></table>{rows.length === 0 && <div className="dg-history-empty">{t("没有匹配的政策审计记录。此页面仅管理员可读。", "No matching audit events. This page is available to administrators only.")}</div>}</div>
    </section>
  </div></main></>;
}
