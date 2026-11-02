"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import "./workspace.css";

type Actor = "sales_a" | "sales_b" | "admin" | "manager";
type Policy = {
  id: string; policy_id: string; version: string; title: string; action_type: "quote_discount" | "reference_only"; status: "active" | "inactive";
  max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean; source: string;
};
type Approval = { id: string; run_id: string; applicant_actor_id: string; customer_id: string; product_id: string; quantity: number; discount_pct: string; policy_id: string; policy_version: string; state: string };
type PolicyDraft = { id: string; policy_id: string; version: string; title: string; source: string; action_type: "quote_discount" | "reference_only" | null; max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean; status: string };
type RunResult = {
  run_id: string; status: "completed" | "needs_input" | "failed"; execution_mode: "llm" | "demo";
  intent?: { product_id?: string | null; quantity?: number | null; requested_discount_pct?: string | null } | null;
  decision?: { decision: string; reason_code: string; human_message: string; policy_version?: string | null } | null;
  quote?: { id: string; status: string; breakdown: { currency: string; unit_price: string; total: string; subtotal: string; discount_amount: string; quantity: number; discount_pct: string }; policy_id: string; policy_version: string } | null;
  policy_evidence: { policy_id: string; version: string; title: string; citation: string; source: string; retrieval_mode: string }[];
  trace: { step: string; status: string; message: string; timestamp: string; details?: Record<string, unknown> }[];
  approval_request?: Approval | null; duration_ms?: number | null;
};

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEMO_MODE = process.env.NODE_ENV !== "production";
const examples = [
  { label: "正常 VIP 报价", text: "I'm a VIP customer and want to buy 1,000 units of Product A. Can you give me an 8% discount? Please prepare a quote." },
  { label: "检验政策上限", text: "Please prepare a quote for 1,000 units of Product A with a 5% discount." },
  { label: "缺少报价信息", text: "Please prepare a quote for Product A." },
];
const actorLabels: Record<Actor, string> = { sales_a: "Sales A · VIP 客户", sales_b: "Sales B · Standard 客户", admin: "Demo Admin", manager: "Demo Manager" };

async function apiRequest<T>(path: string, actor: Actor, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (DEMO_MODE) headers.set("X-Demo-Actor", actor);
  if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, {
    ...init, headers, cache: "no-store", credentials: "include",
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload?.detail?.message ?? payload?.detail ?? `Request failed (${response.status})`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return payload as T;
}

export default function HomePage() {
  const [actor, setActor] = useState<Actor>("sales_a");
  const [authenticated, setAuthenticated] = useState(DEMO_MODE);
  const [loginId, setLoginId] = useState("sales_a");
  const [password, setPassword] = useState("");
  const [inquiry, setInquiry] = useState(examples[0].text);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [result, setResult] = useState<RunResult | null>(null);
  const [lastQuoteId, setLastQuoteId] = useState<string | null>(null);
  const [lastQuoteOwner, setLastQuoteOwner] = useState<Actor | null>(null);
  const [ownershipCheck, setOwnershipCheck] = useState<{ read: number; update: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [policyDraft, setPolicyDraft] = useState<PolicyDraft | null>(null);
  const [draftFile, setDraftFile] = useState<File | null>(null);
  const [draftPolicyId, setDraftPolicyId] = useState("DISCOUNT-02");
  const [draftVersion, setDraftVersion] = useState("v1");
  const [draftTitle, setDraftTitle] = useState("New VIP discount rule");
  const [draftActionType, setDraftActionType] = useState<"quote_discount" | "reference_only">("quote_discount");
  const [directLimit, setDirectLimit] = useState("5");
  const [reviewLimit, setReviewLimit] = useState("8");
  const [exceptionEnabled, setExceptionEnabled] = useState(true);
  const [diffBase, setDiffBase] = useState("");
  const [diffCompare, setDiffCompare] = useState("");
  const [diffResult, setDiffResult] = useState<{ base: { version: string; decision: { decision: string; reason_code: string }; breakdown?: { total: string } | null }; compare: { version: string; decision: { decision: string; reason_code: string }; breakdown?: { total: string } | null } } | null>(null);
  const [metrics, setMetrics] = useState<{ run_count: number; allow_count: number; review_count: number; deny_count: number; avg_duration_ms: number | null; p50_duration_ms: number | null; p95_duration_ms: number | null; demo_run_count: number; llm_run_count: number; input_tokens: number | null; output_tokens: number | null } | null>(null);
  const runKeyRef = useRef<{ actor: Actor; inquiry: string; key: string } | null>(null);
  const activePolicy = useMemo(() => policies.find((policy) => policy.status === "active"), [policies]);

  const refreshPolicies = useCallback(async () => {
    try { setPolicies(await apiRequest<Policy[]>("/api/v1/policies", actor)); }
    catch (error) { setNotice(error instanceof Error ? error.message : "无法连接到 API"); }
  }, [actor]);
  useEffect(() => {
    if (DEMO_MODE) return;
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) return; const user = await response.json(); setActor(user.actor_id as Actor); setAuthenticated(true); })
      .catch(() => undefined);
  }, []);
  useEffect(() => { if (authenticated) void refreshPolicies(); }, [refreshPolicies, authenticated]);
  useEffect(() => {
    if (!authenticated) return;
    if (actor === "manager") void apiRequest<Approval[]>("/api/v1/approvals", actor).then(setApprovals).catch((e) => setNotice(e.message));
    else setApprovals([]);
    if (actor === "manager" || actor === "admin") void apiRequest<typeof metrics>("/api/v1/metrics", actor).then(setMetrics).catch(() => setMetrics(null));
    else setMetrics(null);
  }, [actor, authenticated]);

  async function signIn(event: FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      const response = await fetch(`${API}/api/v1/auth/login`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ actor_id: loginId, password }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "登录失败");
      setActor(payload.actor_id as Actor); setAuthenticated(true); setPassword(""); setNotice("已安全登录");
    } catch (error) { setNotice(error instanceof Error ? error.message : "登录失败"); }
    finally { setBusy(false); }
  }

  async function signOut() {
    await fetch(`${API}/api/v1/auth/logout`, { method: "POST", credentials: "include" });
    setAuthenticated(false); setPolicies([]); setResult(null);
  }

  async function runInquiry(event?: FormEvent) {
    event?.preventDefault(); setBusy(true); setNotice(""); setResult(null);
    const key = runKeyRef.current?.actor === actor && runKeyRef.current.inquiry === inquiry
      ? runKeyRef.current.key
      : globalThis.crypto.randomUUID();
    runKeyRef.current = { actor, inquiry, key };
    try {
      const next = await apiRequest<RunResult>("/api/v1/runs", actor, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ inquiry }) });
      setResult(next);
      runKeyRef.current = null;
      if (next.quote) { setLastQuoteId(next.quote.id); setLastQuoteOwner(actor); setOwnershipCheck(null); }
    }
    catch (error) { setNotice(error instanceof Error ? error.message : "询盘运行失败"); }
    finally { setBusy(false); }
  }

  async function activatePolicy(policy: Policy) {
    setBusy(true); setNotice("");
    try {
      await apiRequest(`/api/v1/policies/${policy.id}/activate`, actor, { method: "POST" });
      await refreshPolicies(); setNotice(`${policy.policy_id}@${policy.version} 已激活`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "政策激活失败"); }
    finally { setBusy(false); }
  }

  async function reviewApproval(approval: Approval, approve: boolean) {
    setBusy(true); setNotice("");
    try {
      const outcome = await apiRequest<{ approval: Approval; quote?: RunResult["quote"] | null; trace: RunResult["trace"] }>(`/api/v1/approvals/${approval.id}/${approve ? "approve" : "reject"}`, actor, {
        method: "POST", ...(approve ? {} : { body: JSON.stringify({ reason: "Rejected in demo review" }) }),
      });
      setApprovals((current) => current.filter((item) => item.id !== approval.id));
      if (outcome.quote) {
        setResult((current) => ({ ...(current ?? { run_id: approval.run_id, status: "completed", execution_mode: "demo", policy_evidence: [] }),
          decision: { decision: "ALLOW", reason_code: "APPROVAL_APPROVED", human_message: "经理批准后已重新校验并创建报价草稿。", policy_version: outcome.quote!.policy_version },
          quote: outcome.quote!, trace: outcome.trace }));
        setLastQuoteId(outcome.quote.id);
        setLastQuoteOwner(approval.applicant_actor_id as Actor);
      }
      setNotice(approve ? (outcome.quote ? "审批通过，报价草稿已创建" : `审批状态：${outcome.approval.state}`) : "已拒绝审批请求，未创建报价");
      void apiRequest<typeof metrics>("/api/v1/metrics", actor).then(setMetrics);
    } catch (error) { setNotice(error instanceof Error ? error.message : "审批处理失败"); }
    finally { setBusy(false); }
  }

  async function uploadPolicyDraft(event: FormEvent) {
    event.preventDefault();
    if (!draftFile) { setNotice("请先选择 .md 或 .txt 政策文件"); return; }
    const form = new FormData(); form.set("policy_id", draftPolicyId); form.set("version", draftVersion); form.set("file", draftFile);
    setBusy(true);
    try {
      const created = await apiRequest<PolicyDraft>("/api/v1/policy-drafts", actor, { method: "POST", body: form });
      setPolicyDraft(created); setDraftActionType(created.action_type ?? "quote_discount"); setNotice("政策文本已保存为草稿；它不会直接成为授权规则");
    } catch (error) { setNotice(error instanceof Error ? error.message : "上传失败"); }
    finally { setBusy(false); }
  }

  async function savePolicyDraft(activate: boolean) {
    if (!policyDraft) return;
    setBusy(true);
    try {
      const body = {
        title: draftTitle, action_type: draftActionType, customer_tier: "VIP",
        max_discount_pct: draftActionType === "quote_discount" ? Number(directLimit) : null,
        review_max_discount_pct: draftActionType === "quote_discount" && exceptionEnabled ? Number(reviewLimit) : null,
        exception_approval_allowed: draftActionType === "quote_discount" && exceptionEnabled,
      };
      const saved = await apiRequest<PolicyDraft>(`/api/v1/policy-drafts/${policyDraft.id}`, actor, { method: "PUT", body: JSON.stringify(body) });
      setPolicyDraft(saved);
      if (activate) {
        const active = await apiRequest<PolicyDraft>(`/api/v1/policy-drafts/${policyDraft.id}/activate`, actor, { method: "POST" });
        setPolicyDraft(active); await refreshPolicies(); setNotice(`${active.policy_id}@${active.version} 已激活`);
      } else setNotice("结构化规则已保存为待审草稿");
    } catch (error) { setNotice(error instanceof Error ? error.message : "保存规则失败"); }
    finally { setBusy(false); }
  }

  async function comparePolicies() {
    setBusy(true); setDiffResult(null);
    try {
      setDiffResult(await apiRequest("/api/v1/policy-diffs", actor, { method: "POST", body: JSON.stringify({ inquiry, base_policy_id: diffBase.split("@")[0], base_version: diffBase.split("@")[1], compare_policy_id: diffCompare.split("@")[0], compare_version: diffCompare.split("@")[1] }) }));
    } catch (error) { setNotice(error instanceof Error ? error.message : "版本对比失败"); }
    finally { setBusy(false); }
  }

  async function resetDemo() {
    setBusy(true); setNotice("");
    try {
      await apiRequest("/api/v1/demo/reset", actor, { method: "POST" });
      setResult(null); setLastQuoteId(null); setLastQuoteOwner(null); setOwnershipCheck(null); setApprovals([]); await refreshPolicies();
      setMetrics(await apiRequest("/api/v1/metrics", actor));
      setNotice("演示数据已重置到 v1 初始状态");
    } catch (error) { setNotice(error instanceof Error ? error.message : "重置失败"); }
    finally { setBusy(false); }
  }

  async function tryUnauthorizedQuoteAccess() {
    if (!lastQuoteId) return;
    setBusy(true);
    try {
      const read = await fetch(`${API}/api/v1/quotes/${lastQuoteId}`, { headers: DEMO_MODE ? { "X-Demo-Actor": "sales_b" } : {}, credentials: "include", cache: "no-store" });
      const update = await fetch(`${API}/api/v1/quotes/${lastQuoteId}`, {
        method: "PATCH", headers: DEMO_MODE ? { "Content-Type": "application/json", "X-Demo-Actor": "sales_b" } : { "Content-Type": "application/json" }, credentials: "include",
        body: JSON.stringify({ requested_discount_pct: 0 }), cache: "no-store",
      });
      setOwnershipCheck({ read: read.status, update: update.status });
    } catch (error) { setNotice(error instanceof Error ? error.message : "资源归属检查失败"); }
    finally { setBusy(false); }
  }

  async function downloadQuoteCsv(quoteId: string) {
    const response = await fetch(`${API}/api/v1/quotes/${quoteId}/export.csv`, { headers: DEMO_MODE ? { "X-Demo-Actor": actor } : {}, credentials: "include", cache: "no-store" });
    if (!response.ok) { setNotice("CSV 导出失败或当前身份无权访问"); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = `dealguard-${quoteId}.csv`; link.click(); URL.revokeObjectURL(url);
  }

  if (!authenticated) return <main className="dg-app dg-login-shell"><section className="dg-login-card"><div className="dg-brand-mark" aria-hidden="true">D</div><h1>登录 DealGuard</h1><p>使用管理员提供的账户登录。</p><form onSubmit={signIn} className="dg-login-form"><label>账户 ID<input value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="username" required /></label><label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label><button className="dg-button" disabled={busy}>登录</button>{notice && <p role="status">{notice}</p>}</form></section></main>;

  return (
    <main className="dg-app">
      <header className="dg-topbar">
        <div className="dg-topbar-inner">
          <div className="dg-brand">
            <div className="dg-brand-mark" aria-hidden="true">D</div>
            <div><div className="dg-brand-name">DealGuard</div><div className="dg-brand-subtitle">Policy-aware transaction workspace</div></div>
            <span className="dg-demo-tag">{DEMO_MODE ? "DEMO MODE" : "AUTHENTICATED"}</span>
          </div>
          <nav className="dg-top-nav" aria-label="主导航"><Link href="/runs">运行记录</Link>{(actor === "manager" || actor === "admin") && <Link href="/evaluation">检索评测</Link>}{actor === "admin" && <Link href="/audit">政策审计</Link>}</nav>
          <div className="dg-header-actions">
            {DEMO_MODE ? <label className="dg-actor"><span>演示身份</span>
              <select value={actor} onChange={(event) => { setActor(event.target.value as Actor); setResult(null); }}>
                {Object.entries(actorLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label> : <span className="dg-actor">{actorLabels[actor]}</span>}
            {!DEMO_MODE && <button onClick={() => void signOut()} className="dg-reset">退出登录</button>}
            {actor === "admin" && <button onClick={resetDemo} disabled={busy} className="dg-reset">重置演示数据</button>}
          </div>
        </div>
      </header>

      <div className="dg-main">
        <div className="dg-page-head">
          <div><h1>询盘与报价工作台</h1><p>检索提供决策依据，确定性规则负责授权。所有报价均为草稿，不会提交真实交易。</p></div>
          <div className="dg-current-policy"><span className="dg-policy-dot" /><span>当前政策</span><strong>{activePolicy ? `${activePolicy.policy_id}@${activePolicy.version}` : "载入中"}</strong></div>
        </div>
        {notice && <div role="status" className="dg-notice">{notice}</div>}

        {actor === "manager" && <section className="dg-manager">
          <div className="dg-manager-head"><h2>待处理审批 · {approvals.length}</h2><span className="dg-manager-note">批准时会重新检查政策、归属和库存</span></div>
          {approvals.length === 0 ? <p className="dg-help">当前没有待处理申请。</p> : <div className="dg-approval-list">{approvals.map((approval) => <article key={approval.id} className="dg-approval">
            <div><p className="dg-approval-title">{approval.customer_id} · {approval.product_id}</p><p className="dg-approval-detail">{approval.quantity.toLocaleString()} 件 · {approval.discount_pct}% · {approval.policy_id}@{approval.policy_version}</p></div>
            <div className="dg-approval-actions"><button onClick={() => void reviewApproval(approval, true)} disabled={busy} className="dg-button dg-button-small">批准</button><button onClick={() => void reviewApproval(approval, false)} disabled={busy} className="dg-button dg-button-small dg-button-reject">拒绝</button></div>
          </article>)}</div>}
        </section>}

        {(actor === "admin" || actor === "manager") && metrics && <section className="dg-metrics" aria-label="运行指标">
          <div className="dg-metric"><div className="dg-metric-label">运行总数</div><div className="dg-metric-value">{metrics.run_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">允许 / 审批 / 拒绝</div><div className="dg-metric-value">{metrics.allow_count} / {metrics.review_count} / {metrics.deny_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">耗时 P50 / P95</div><div className="dg-metric-value">{metrics.p50_duration_ms == null ? "未知" : `${metrics.p50_duration_ms} / ${metrics.p95_duration_ms} ms`}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">执行模式</div><div className="dg-metric-value">Demo {metrics.demo_run_count} / LLM {metrics.llm_run_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">Token 用量</div><div className="dg-metric-value">{metrics.input_tokens == null ? "未知（Demo/未提供）" : `${metrics.input_tokens} in / ${metrics.output_tokens ?? 0} out`}</div></div>
        </section>}

        <div className="dg-workspace">
          <section className="dg-panel dg-panel-pad">
            <div className="dg-panel-heading"><div><div className="dg-kicker">01 · Inquiry</div><h2>客户询盘</h2></div><span className="dg-language">English input</span></div>
            <div className="dg-examples">{examples.map((example) => <button type="button" key={example.label} onClick={() => setInquiry(example.text)} aria-pressed={inquiry === example.text} className={`dg-example ${inquiry === example.text ? "dg-example-active" : ""}`}>{example.label}</button>)}</div>
            <form onSubmit={runInquiry} aria-busy={busy}>
              <label htmlFor="inquiry" className="dg-field-label">询盘内容</label>
              <textarea id="inquiry" value={inquiry} onChange={(event) => setInquiry(event.target.value)} rows={8} className="dg-textarea" placeholder="Tell us the product, quantity and requested discount..." />
              <button type="submit" disabled={busy || !inquiry.trim()} className="dg-button dg-button-primary">{busy && <span className="dg-spinner" aria-hidden="true" />}{busy ? "正在分析询盘…" : "运行询盘"}</button>
            </form>
            <p className="dg-help">客户等级、商品价格和库存由后端演示数据提供。文本中自报的 VIP 身份不会用于授权。</p>
          </section>

          <section className="dg-panel dg-panel-pad">
            <div className="dg-panel-heading"><div><div className="dg-kicker">02 · Decision</div><h2>执行结果</h2></div>{result && <span className="dg-kicker">{result.execution_mode === "demo" ? "DEMO" : "LLM"}</span>}</div>
            {!result ? <div className="dg-empty">
              <div className="dg-empty-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M7 17 17 7M8 7h9v9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></div>
              <p className="dg-empty-title">等待运行询盘</p><p className="dg-empty-copy">结构化请求、Guard 判定和报价草稿会显示在这里。</p>
            </div> : <div className="dg-result">
              <div className={`dg-decision dg-decision-${result.decision?.decision ?? "DENY"}`}>
                <div className="dg-decision-main"><span className="dg-decision-mark" aria-hidden="true">{result.decision?.decision === "ALLOW" ? "✓" : result.decision?.decision === "REVIEW" ? "↗" : result.decision?.decision === "NEEDS_INPUT" ? "!" : "×"}</span><strong className="dg-decision-name">{result.decision?.decision ?? result.status}</strong></div>
                <span className="dg-decision-code">{result.decision?.reason_code}</span>
              </div>
              <p className="dg-message">{result.decision?.human_message ?? "工作流未返回决策。"}</p>
              {result.intent && <div className="dg-intent"><div><div className="dg-intent-label">商品</div><div className="dg-intent-value">{result.intent.product_id ?? "—"}</div></div><div><div className="dg-intent-label">数量</div><div className="dg-intent-value">{result.intent.quantity?.toLocaleString() ?? "—"}</div></div><div><div className="dg-intent-label">折扣</div><div className="dg-intent-value">{result.intent.requested_discount_pct ? `${result.intent.requested_discount_pct}%` : "—"}</div></div></div>}
              {result.quote && <div className="dg-quote">
                <div className="dg-quote-top"><div><div className="dg-quote-label">Quote draft · {result.quote.breakdown.currency}</div><div className="dg-quote-total">{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote.breakdown.currency }).format(Number(result.quote.breakdown.total))}</div><div className="dg-quote-meta">{result.quote.breakdown.quantity.toLocaleString()} units · {result.quote.breakdown.discount_pct}% discount</div></div><div className="dg-quote-policy">政策版本<strong>{result.quote.policy_id}@{result.quote.policy_version}</strong></div></div>
                <div className="dg-quote-footer"><span className="dg-quote-id">{result.quote.id}</span>{(actor === "admin" || actor === lastQuoteOwner) && <button onClick={() => void downloadQuoteCsv(result.quote!.id)} className="dg-button dg-button-small dg-button-secondary">导出 CSV</button>}</div>
              </div>}
              {result.approval_request && <div className="dg-block"><p className="dg-block-title">经理审批</p><div className="dg-policy-row dg-policy-row-active"><div className="dg-policy-row-top"><strong className="dg-policy-id">{result.approval_request.policy_id}@{result.approval_request.policy_version}</strong><span className="dg-status">待处理</span></div><p className="dg-policy-limit">审批请求已保存；批准前不会生成报价草稿。</p></div></div>}
              <div className="dg-block"><p className="dg-block-title">Policy evidence</p>{result.policy_evidence.length ? result.policy_evidence.map((evidence) => <div key={evidence.source} className="dg-evidence"><div className="dg-evidence-head"><strong className="dg-evidence-title">{evidence.title}</strong><span className="dg-evidence-source">{evidence.source}</span></div><p className="dg-evidence-copy">{evidence.citation}</p><div className="dg-evidence-mode">Retrieved via {evidence.retrieval_mode}</div></div>) : <p className="dg-help">本次没有适用的政策依据</p>}</div>
              {lastQuoteId && <div className="dg-block"><div className="dg-policy-row"><div className="dg-policy-row-top"><strong className="dg-policy-id">资源归属检查</strong><button onClick={tryUnauthorizedQuoteAccess} disabled={busy} className="dg-button dg-button-small dg-button-secondary">模拟越权操作</button></div><p className="dg-policy-limit">使用 Sales B 尝试读取并修改最近一份报价草稿。</p>{ownershipCheck && <p role="status" className="dg-help">已拦截：读取 HTTP {ownershipCheck.read}，修改 HTTP {ownershipCheck.update}。报价内容未改变。</p>}</div></div>}
            </div>}
          </section>

          <aside className="dg-side dg-column">
            <section className="dg-panel dg-panel-pad">
              <div className="dg-panel-heading"><div><div className="dg-kicker">03 · Policy center</div><h2>政策版本</h2></div></div>
              <div className="dg-policy-list">{policies.map((policy) => <article key={policy.id} className={`dg-policy-row ${policy.status === "active" ? "dg-policy-row-active" : ""}`}>
                <div className="dg-policy-row-top"><strong className="dg-policy-id">{policy.policy_id}@{policy.version}</strong><span className={`dg-status ${policy.status === "active" ? "dg-status-active" : ""}`}>{policy.status}</span></div>
                <p className="dg-policy-limit">{policy.action_type === "quote_discount" ? <>折扣报价 · 直接上限 <strong>{policy.max_discount_pct}%</strong>{policy.review_max_discount_pct && <> · 审批上限 <strong>{policy.review_max_discount_pct}%</strong></>}</> : "仅供检索参考，不授权报价"}</p>
                {actor === "admin" && policy.status !== "active" && <div className="dg-policy-actions"><button onClick={() => activatePolicy(policy)} disabled={busy} className="dg-button dg-button-small dg-button-secondary">激活此版本</button></div>}
              </article>)}</div>
              {actor !== "admin" && <p className="dg-policy-only-admin">仅 Demo Admin 可以激活预置版本。</p>}

              {actor === "admin" && <form onSubmit={uploadPolicyDraft} className="dg-upload"><h3>上传政策草稿</h3><p className="dg-help">UTF-8 Markdown / TXT，最大 1 MiB。上传内容不会直接成为授权规则。</p>
                <div className="dg-two-fields"><input aria-label="政策编号" value={draftPolicyId} onChange={(event) => setDraftPolicyId(event.target.value)} className="dg-control"/><input aria-label="政策版本" value={draftVersion} onChange={(event) => setDraftVersion(event.target.value)} className="dg-control"/></div>
                <input aria-label="政策文本文件" type="file" accept=".md,.txt,text/plain" onChange={(event) => setDraftFile(event.target.files?.[0] ?? null)} />
                <button disabled={busy} className="dg-button dg-button-small dg-button-secondary">保存来源草稿</button>
              </form>}
              {actor === "admin" && policyDraft && <div className="dg-upload"><h3>{policyDraft.policy_id}@{policyDraft.version} · {policyDraft.status}</h3>
                <div className="dg-rule-fields"><label>政策标题<input aria-label="政策标题" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} className="dg-control"/></label><label>授权范围<select aria-label="政策动作范围" value={draftActionType} onChange={(event) => setDraftActionType(event.target.value as "quote_discount" | "reference_only")} className="dg-control"><option value="quote_discount">折扣报价规则</option><option value="reference_only">仅供检索参考</option></select></label>{draftActionType === "quote_discount" && <><label>直接折扣上限 %<input type="number" min="0" max="100" value={directLimit} onChange={(event) => setDirectLimit(event.target.value)} className="dg-control"/></label><label className="dg-checkbox"><input type="checkbox" checked={exceptionEnabled} onChange={(event) => setExceptionEnabled(event.target.checked)}/>允许例外审批</label>{exceptionEnabled && <label>审批折扣上限 %<input type="number" min="0" max="100" value={reviewLimit} onChange={(event) => setReviewLimit(event.target.value)} className="dg-control"/></label>}</>}</div>
                <div className="dg-draft-actions"><button onClick={() => void savePolicyDraft(false)} disabled={busy || policyDraft.status === "active"} className="dg-button dg-button-small dg-button-secondary">保存规则</button><button onClick={() => void savePolicyDraft(true)} disabled={busy || policyDraft.status === "active"} className="dg-button dg-button-small">保存并激活</button></div>
              </div>}

              <div className="dg-divider"><h3 className="dg-block-title">策略版本 Diff</h3><p className="dg-diff-copy">使用左侧同一询盘与可信业务数据，只比较 Guard 结果，不创建报价。</p>
                <div className="dg-diff-controls">{[diffBase, diffCompare].map((value, index) => <select key={index} aria-label={index === 0 ? "基准政策" : "对比政策"} value={value} onChange={(event) => index === 0 ? setDiffBase(event.target.value) : setDiffCompare(event.target.value)} className="dg-control"><option value="">选择版本</option>{policies.map((policy) => <option key={policy.id} value={`${policy.policy_id}@${policy.version}`}>{policy.policy_id}@{policy.version}</option>)}</select>)}</div>
                <button onClick={comparePolicies} disabled={busy || !diffBase || !diffCompare} className="dg-button dg-button-small dg-button-secondary" style={{ width: "100%", marginTop: 8 }}>比较当前询盘</button>
                {diffResult && <div className="dg-diff-result">{[diffResult.base, diffResult.compare].map((side) => <div key={side.version} className="dg-diff-side"><strong>{side.version}: {side.decision.decision}</strong><p>{side.decision.reason_code}</p>{side.breakdown && <p>${Number(side.breakdown.total).toLocaleString()}</p>}</div>)}</div>}
              </div>
            </section>

            <section className="dg-panel dg-panel-pad"><div className="dg-panel-heading"><div><div className="dg-kicker">04 · Trace</div><h2>运行轨迹</h2></div></div>
              {!result?.trace.length ? <p className="dg-help">运行后的程序事件会显示在这里。</p> : <ol className="dg-trace">{result.trace.map((event, index) => <li key={`${event.step}-${index}`} className="dg-trace-item"><span className="dg-trace-dot"/><div><div className="dg-trace-top"><strong className="dg-trace-step">{event.step.replaceAll("_", " ")}</strong><span className="dg-trace-status">{event.status}</span>{typeof event.details?.duration_ms === "number" && <span className="dg-trace-status">{event.details.duration_ms} ms</span>}</div><p className="dg-trace-message">{event.message}</p></div></li>)}</ol>}
            </section>
          </aside>
        </div>
        <footer className="dg-footer"><p>HarborLight Trading · USD · simulated business data</p><p>{result ? `Run ${result.run_id} · ${result.execution_mode === "demo" ? "Deterministic Demo" : "Configured LLM"}` : "Retrieval informs decisions; deterministic policies authorize actions."}</p></footer>
      </div>
    </main>
  );
}
