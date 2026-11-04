"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import "../history.css";
import "./policy-impact.css";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { localizedDecision, localizedReason, localizedStatus, useLanguage } from "../../components/LanguageProvider";

type PolicyDraft = {
  id: string; policy_id: string; version: string; title: string; action_type: string | null;
  customer_tier: string; max_discount_pct: number | null; status: string;
};
type RunSummary = {
  run_id: string; status: string; execution_mode: string; decision: string | null;
  reason_code: string | null; policy_id: string | null; policy_version: string | null;
  created_at: string;
};
type TrustedSnapshot = {
  customer?: { id?: string; tier?: string };
  policy?: { policy_id?: string; version?: string; source?: string };
  lines?: { proposal?: { product_id?: string; quantity?: number; requested_discount_pct?: string };
    product?: { id?: string; name?: string; currency?: string; unit_price?: string; stock?: number };
    policy_evidence?: { citation?: string | null; citation_ref?: string | null; chunk_id?: string | null; policy_source_hash?: string | null } | null }[];
};
type ImpactCase = {
  run_id: string; status: "replayed" | "skipped" | "failed"; baseline_decision?: string;
  candidate_decision?: string; baseline_reason_code?: string; candidate_reason_code?: string;
  changed?: boolean; skip_reason?: string; details?: { trusted_snapshot?: TrustedSnapshot };
};
type Report = {
  mode: "frozen_intent_replay" | "end_to_end_replay"; policy_id: string; candidate_version: string;
  total_cases: number; replayed_cases: number; skipped_cases: number; changed_cases: number;
  change_rate: number | null; transitions: Record<string, number>; cases: ImpactCase[]; writes_created: false;
  candidate_title: string; candidate_source: string;
};

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function PolicyImpactPage() {
  const { locale, t } = useLanguage();
  const [drafts, setDrafts] = useState<PolicyDraft[]>([]);
  const [draftId, setDraftId] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [mode, setMode] = useState<Report["mode"]>("frozen_intent_replay");
  const [report, setReport] = useState<Report | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const loadDrafts = useCallback(async () => {
    const response = await fetch(`${API}/api/v1/policy-drafts`, { credentials: "include", cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取政策草稿");
    setDrafts(payload);
    setDraftId((current) => current || payload[0]?.id || "");
  }, []);

  useEffect(() => {
    void loadDrafts().catch((error) => setNotice(error instanceof Error ? error.message : "无法读取政策草稿"));
    void fetch(`${API}/api/v1/runs?limit=50`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取历史询盘");
        const items = payload.items as RunSummary[];
        setRuns(items);
        setSelectedRunIds(items.map((item) => item.run_id));
      })
      .catch((error) => setNotice(error instanceof Error ? error.message : "无法读取历史询盘"));
  }, [loadDrafts]);

  function toggleRun(runId: string) {
    setSelectedRunIds((current) => current.includes(runId)
      ? current.filter((id) => id !== runId)
      : [...current, runId]);
  }

  async function simulate(event: FormEvent) {
    event.preventDefault();
    if (!draftId) return;
    setBusy(true); setNotice(""); setReport(null);
    try {
      const response = await fetch(`${API}/api/v1/policy-impact/simulate`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy_draft_id: draftId, mode, run_ids: selectedRunIds }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "影响分析运行失败");
      setReport(payload);
    } catch (error) { setNotice(error instanceof Error ? error.message : "影响分析运行失败"); }
    finally { setBusy(false); }
  }

  const selectedDraft = drafts.find((draft) => draft.id === draftId);
  const maxTransition = Math.max(1, ...Object.values(report?.transitions ?? {}));
  return <><WorkspaceNav /><main className="dg-history"><div className="dg-history-main dg-impact-main">
    <header className="dg-history-head"><div><h1>{t("政策影响分析", "Policy impact")}</h1><p>{t("用已保存的询盘回放候选政策，检查决策变化及其原因，再决定是否激活。", "Replay saved inquiries against a candidate policy, inspect decision changes and reasons, then decide whether to activate it.")}</p></div></header>

    <section className="dg-history-panel dg-impact-controls">
      <form className="dg-impact-form" onSubmit={(event) => void simulate(event)}>
        <label>{t("候选政策草稿", "Candidate policy draft")}
          <select value={draftId} onChange={(event) => setDraftId(event.target.value)} required>
            {drafts.length === 0 && <option value="">{t("没有可分析的政策草稿", "No policy drafts available")}</option>}
            {drafts.map((draft) => <option key={draft.id} value={draft.id}>{draft.policy_id} · {draft.version} — {draft.title}</option>)}
          </select>
        </label>
        <label>{t("回放方式", "Replay mode")}
          <select value={mode} onChange={(event) => setMode(event.target.value as Report["mode"])}>
            <option value="frozen_intent_replay">{t("冻结事实回放（快速，可复现）", "Frozen fact replay (fast, reproducible)")}</option>
            <option value="end_to_end_replay">{t("完整工作流回放（包含检索与解析）", "End-to-end replay (retrieval and parsing)")}</option>
          </select>
        </label>
        <button className="dg-history-button dg-impact-run" disabled={busy || !draftId || selectedRunIds.length === 0}>{busy ? t("正在分析…", "Analyzing…") : t("运行影响分析", "Run impact analysis")}</button>
      </form>
      {selectedDraft && <div className="dg-impact-candidate"><span>{t("候选版本", "Candidate version")}</span><strong>{selectedDraft.policy_id}@{selectedDraft.version}</strong><span>{selectedDraft.action_type === "quote_discount" ? t(`${selectedDraft.customer_tier} 折扣上限 ${selectedDraft.max_discount_pct ?? "—"}%`, `${selectedDraft.customer_tier} discount limit ${selectedDraft.max_discount_pct ?? "—"}%`) : selectedDraft.title}</span></div>}
      {notice && <p className="dg-history-notice" role="alert">{notice}</p>}
      {drafts.length === 0 && !notice && <p className="dg-impact-empty">{t("先在政策管理中创建并保存一个未激活的结构化政策草稿。", "Create and save an inactive structured policy draft in Policy Audit first.")}</p>}
    </section>

    <section className="dg-history-panel dg-impact-run-selection">
      <div className="dg-impact-section-head">
        <div><h2>{t("选择历史询盘", "Select historical inquiries")}</h2><p>{t("最多载入最近 50 条；默认全选，可按需缩小回放范围。", "Up to 50 recent inquiries are loaded and selected by default. Narrow the replay set as needed.")}</p></div>
        <div className="dg-impact-selection-actions">
          <span>{t(`已选 ${selectedRunIds.length} / ${runs.length}`, `${selectedRunIds.length} / ${runs.length} selected`)}</span>
          <button type="button" onClick={() => setSelectedRunIds(runs.map((run) => run.run_id))} disabled={runs.length === 0}>{t("全选", "Select all")}</button>
          <button type="button" onClick={() => setSelectedRunIds([])} disabled={selectedRunIds.length === 0}>{t("清空", "Clear")}</button>
        </div>
      </div>
      {runs.length ? <div className="dg-history-table-wrap"><table className="dg-history-table"><thead><tr><th>{t("选择", "Select")}</th><th>{t("时间", "Time")}</th><th>{t("运行记录", "Run")}</th><th>{t("决策", "Decision")}</th><th>{t("政策版本", "Policy version")}</th><th>{t("模式", "Mode")}</th></tr></thead><tbody>
        {runs.map((run) => <tr key={run.run_id}>
          <td><input aria-label={t(`选择询盘 ${run.run_id}`, `Select inquiry ${run.run_id}`)} type="checkbox" checked={selectedRunIds.includes(run.run_id)} onChange={() => toggleRun(run.run_id)} /></td>
          <td>{new Date(run.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}</td><td><code>{run.run_id.slice(0, 12)}</code></td>
          <td>{localizedDecision(run.decision, locale) || localizedStatus(run.status, locale)}<small>{localizedReason(run.reason_code, locale)}</small></td>
          <td>{run.policy_id ? `${run.policy_id}@${run.policy_version ?? "—"}` : "—"}</td><td>{run.execution_mode}</td>
        </tr>)}
      </tbody></table></div> : <p className="dg-impact-empty">{t("当前工作区没有可回放的历史询盘。", "No historical inquiries are available for replay in this workspace.")}</p>}
    </section>

    {report && <>
      <section className="dg-impact-summary" aria-label={t("影响分析摘要", "Impact summary")}>
        <article><span>{t("历史询盘", "Historical inquiries")}</span><strong>{report.total_cases}</strong></article>
        <article><span>{t("成功回放", "Replayed")}</span><strong>{report.replayed_cases}</strong></article>
        <article><span>{t("决策发生变化", "Changed decisions")}</span><strong>{report.changed_cases}</strong></article>
        <article><span>{t("变化率", "Change rate")}</span><strong>{report.change_rate === null ? "—" : `${(report.change_rate * 100).toFixed(1)}%`}</strong></article>
      </section>

      <section className="dg-history-panel dg-impact-transitions">
        <div className="dg-impact-section-head"><div><h2>{t("决策变化", "Decision changes")}</h2><p>{t("只显示候选政策导致决策或原因码变化的询盘。", "Only inquiries whose decision or reason code changed under the candidate policy are shown.")}</p></div><span>{report.writes_created ? t("写入已创建", "Writes created") : t("只读回放 · 未产生写入", "Read-only replay · no writes created")}</span></div>
        {Object.entries(report.transitions).length ? <div className="dg-impact-bars">{Object.entries(report.transitions).map(([transition, count]) => <div className="dg-impact-bar-row" key={transition}><span>{transition.split("→").map((value) => localizedDecision(value, locale)).join(" → ")}</span><div><i style={{ width: `${Math.max(8, count / maxTransition * 100)}%` }} /></div><strong>{count}</strong></div>)}</div> : <p className="dg-impact-empty">{t("本次回放中没有决策变化。", "No decision changes were found in this replay.")}</p>}
        <details className="dg-impact-source"><summary>{t("查看候选政策原文", "View candidate policy source")}</summary><p>{report.candidate_title} · {report.policy_id}@{report.candidate_version}</p><pre>{report.candidate_source}</pre></details>
      </section>

      <section className="dg-history-panel dg-impact-cases">
        <div className="dg-impact-section-head"><div><h2>{t("逐条回放结果", "Replay results")}</h2><p>{t("每条记录保留基线与候选决策，跳过或失败项展示稳定原因码。", "Each case retains baseline and candidate decisions. Skipped or failed cases show stable reason codes.")}</p></div><span>{report.mode === "frozen_intent_replay" ? t("冻结事实", "Frozen facts") : t("完整工作流", "End-to-end workflow")}</span></div>
        <div className="dg-history-table-wrap"><table className="dg-history-table"><thead><tr><th>{t("运行记录", "Run")}</th><th>{t("状态", "Status")}</th><th>{t("当前决策", "Current decision")}</th><th>{t("候选决策", "Candidate decision")}</th><th>{t("变化", "Changed")}</th><th>{t("原因 / 详情", "Reason / details")}</th></tr></thead><tbody>
          {report.cases.map((item) => {
            const snapshot = item.details?.trusted_snapshot;
            return (
              <tr key={item.run_id}>
                <td><code>{item.run_id.slice(0, 12)}</code></td>
                <td>{localizedStatus(item.status, locale)}</td>
                <td>{localizedDecision(item.baseline_decision, locale)}</td>
                <td>{localizedDecision(item.candidate_decision, locale)}</td>
                <td>{item.changed ? t("是", "Yes") : item.status === "replayed" ? t("否", "No") : "—"}</td>
                <td>
                  <span>{localizedReason(item.candidate_reason_code ?? item.skip_reason, locale)}</span>
                  {snapshot && <details className="dg-impact-detail">
                    <summary>{t("查看可信快照与引用", "View trusted snapshot and citations")}</summary>
                    <p>客户等级：{snapshot.customer?.tier ?? "—"} · 基线政策：{snapshot.policy?.policy_id ?? "—"}@{snapshot.policy?.version ?? "—"}</p>
                    {snapshot.lines?.map((line, index) => <article key={`${line.product?.id ?? "item"}-${index}`}>
                      <strong>{line.product?.name ?? line.product?.id ?? "商品"}</strong>
                      <span>{line.proposal?.quantity ?? "—"} 件 · 折扣 {line.proposal?.requested_discount_pct ?? "—"}% · 单价 {line.product?.currency ?? ""} {line.product?.unit_price ?? "—"} · 快照库存 {line.product?.stock ?? "—"}</span>
                      {line.policy_evidence?.citation && <blockquote>{line.policy_evidence.citation}</blockquote>}
                      <small>引用：{line.policy_evidence?.citation_ref ?? "无"} · 片段：{line.policy_evidence?.chunk_id ?? "无"}</small>
                      {line.policy_evidence?.policy_source_hash && <small>政策文本 SHA-256：{line.policy_evidence.policy_source_hash}</small>}
                    </article>)}
                  </details>}
                </td>
              </tr>
            );
          })}
        </tbody></table>{report.cases.length === 0 && <div className="dg-impact-empty">当前工作区还没有可用于回放的历史询盘。</div>}</div>
      </section>
    </>}
  </div></main></>;
}
