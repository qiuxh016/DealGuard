"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import "./workspace.css";

type Actor = string;
type PolicyScope = "general" | "bulk_order" | "loyalty" | "early_payment" | "seasonal_campaign" | "product_specific";
type Policy = {
  id: string; policy_id: string; version: string; title: string; action_type: "quote_discount" | "reference_only"; status: "active" | "inactive";
  policy_scope: PolicyScope; scope_product_id: string | null;
  max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean; source: string;
};
type Approval = { id: string; run_id: string; applicant_actor_id: string; customer_id: string; product_id: string; quantity: number; discount_pct: string; policy_id: string; policy_version: string; state: string };
type WorkspaceMember = { actor_id: string; display_name: string; email: string | null; role: string; customer_id: string | null; is_active: boolean };
type WorkspaceInvitation = { id: string; email: string; role: string; customer_id: string | null; expires_at: string; expired: boolean };
type PolicyDraft = { id: string; policy_id: string; version: string; title: string; source: string; action_type: "quote_discount" | "reference_only" | null; policy_scope: PolicyScope; scope_product_id: string | null; customer_tier: "VIP" | "Standard"; max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean; status: string };
type PolicyDiffSide = { version: string; decision: { decision: string; reason_code: string }; breakdown?: { total: string } | null; lines?: { product_id: string; decision: { decision: string; reason_code: string }; breakdown?: { total: string } | null }[] };
type RunResult = {
  run_id: string; status: "completed" | "needs_input" | "failed"; execution_mode: "llm" | "demo";
  intent?: { product_id?: string | null; quantity?: number | null; requested_discount_pct?: string | null; policy_scope?: PolicyScope | null; items?: { product_id: string; quantity: number; requested_discount_pct: string }[] } | null;
  decision?: { decision: string; reason_code: string; human_message: string; policy_version?: string | null; suggested_discount_pct?: string | null; suggested_product_id?: string | null } | null;
  quote?: { id: string; status: string; breakdown: { currency: string; unit_price: string; total: string; subtotal: string; discount_amount: string; quantity: number; discount_pct: string }; policy_id: string; policy_version: string; lines?: { product_id: string; product_name: string; quantity: number; unit_price: string; discount_pct: string; total: string; policy_id: string; policy_version: string; policy_source: string; citation?: string | null; citation_ref?: string | null; chunk_id?: string | null; policy_source_hash?: string | null; authorization_rule?: Record<string, unknown>; guard_evaluation?: { decision?: string; reason_code?: string; checks?: { check: string; result: string }[] }; available_stock?: number | null; inventory_checked_at?: string | null }[] } | null;
  policy_evidence: { policy_id: string; version: string; title: string; citation: string; source: string; citation_ref: string; chunk_id: string; line_start: number; line_end: number; retrieval_mode: string }[];
  trace: { step: string; status: string; message: string; timestamp: string; details?: Record<string, unknown> }[];
  approval_request?: Approval | null; duration_ms?: number | null;
};

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const policyScopeLabels: Record<PolicyScope, string> = { general: "通用折扣", bulk_order: "批量采购", loyalty: "忠诚客户", early_payment: "提前付款", seasonal_campaign: "季节活动", product_specific: "指定商品" };
const traceLabels: Record<string, string> = {
  parse_intent: "Parse inquiry", load_business_data: "Business data", retrieve_policy: "Retrieve policy",
  validate_proposal: "Guard validation", create_quote_draft: "Create draft", request_approval: "Manager review",
};
const retrievalModeLabels: Record<string, string> = {
  registry: "政策 Registry",
  registry_abstain: "Registry 安全弃权",
  conflict_graph_abstain: "政策冲突拦截",
};

async function apiRequest<T>(path: string, actor: Actor, init?: RequestInit): Promise<T> {
  void actor;
  const headers = new Headers(init?.headers);
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

function inquiryWithSuggestedDiscount(intent: NonNullable<RunResult["intent"]>, productId: string, discount: string): string {
  const normalizedDiscount = Number(discount).toString();
  if (intent.items?.length) {
    return `Please quote ${intent.items.map((item) => `${item.product_id} ${item.quantity} units at ${item.product_id === productId ? normalizedDiscount : item.requested_discount_pct}%`).join("; ")}.`;
  }
  if (intent.product_id && intent.quantity) return `Please quote ${intent.product_id} ${intent.quantity} units at ${normalizedDiscount}%.`;
  return "";
}

export default function HomePage() {
  const [actor, setActor] = useState<Actor>("");
  const [actorRole, setActorRole] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [checkingSession, setCheckingSession] = useState(true);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [loginId, setLoginId] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [registerName, setRegisterName] = useState("");
  const [password, setPassword] = useState("");
  const [inquiry, setInquiry] = useState("");
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [customers, setCustomers] = useState<{ id: string; name: string; tier: "VIP" | "Standard" }[]>([]);
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [products, setProducts] = useState<{ id: string; name: string; currency: string; unit_price: string; stock: number }[]>([]);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"sales" | "manager">("sales");
  const [inviteCustomerId, setInviteCustomerId] = useState("");
  const [invitationLink, setInvitationLink] = useState("");
  const [passwordResetLink, setPasswordResetLink] = useState("");
  const [newCustomerName, setNewCustomerName] = useState("");
  const [newCustomerTier, setNewCustomerTier] = useState<"VIP" | "Standard">("Standard");
  const [newProduct, setNewProduct] = useState({ id: "", name: "", currency: "USD", unit_price: "", stock: "" });
  const [result, setResult] = useState<RunResult | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<number | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [policyDraft, setPolicyDraft] = useState<PolicyDraft | null>(null);
  const [draftFile, setDraftFile] = useState<File | null>(null);
  const [draftPolicyId, setDraftPolicyId] = useState("DISCOUNT-02");
  const [draftVersion, setDraftVersion] = useState("v1");
  const [draftTitle, setDraftTitle] = useState("New VIP discount rule");
  const [draftActionType, setDraftActionType] = useState<"quote_discount" | "reference_only">("quote_discount");
  const [draftPolicyScope, setDraftPolicyScope] = useState<PolicyScope>("general");
  const [draftCustomerTier, setDraftCustomerTier] = useState<"VIP" | "Standard">("VIP");
  const [draftScopeProductId, setDraftScopeProductId] = useState("P-100");
  const [directLimit, setDirectLimit] = useState("5");
  const [reviewLimit, setReviewLimit] = useState("8");
  const [exceptionEnabled, setExceptionEnabled] = useState(true);
  const [diffBase, setDiffBase] = useState("");
  const [diffCompare, setDiffCompare] = useState("");
  const [diffResult, setDiffResult] = useState<{ base: PolicyDiffSide; compare: PolicyDiffSide } | null>(null);
  const [metrics, setMetrics] = useState<{ run_count: number; allow_count: number; review_count: number; deny_count: number; avg_duration_ms: number | null; p50_duration_ms: number | null; p95_duration_ms: number | null; demo_run_count: number; llm_run_count: number; input_tokens: number | null; output_tokens: number | null } | null>(null);
  const runKeyRef = useRef<{ actor: Actor; inquiry: string; key: string } | null>(null);
  const activePolicy = useMemo(() => policies.find((policy) => policy.status === "active"), [policies]);
  const decisionTotal = metrics ? metrics.allow_count + metrics.review_count + metrics.deny_count : 0;
  const decisionShares = metrics && decisionTotal > 0 ? {
    allow: metrics.allow_count / decisionTotal * 100,
    review: metrics.review_count / decisionTotal * 100,
    deny: metrics.deny_count / decisionTotal * 100,
  } : { allow: 0, review: 0, deny: 0 };

  const refreshPolicies = useCallback(async () => {
    try { setPolicies(await apiRequest<Policy[]>("/api/v1/policies", actor)); }
    catch (error) { setNotice(error instanceof Error ? error.message : "无法连接到 API"); }
  }, [actor]);
  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) return; const user = await response.json(); setActor(user.actor_id); setActorRole(user.role); setDisplayName(user.display_name); setAuthenticated(true); })
      .catch(() => undefined).finally(() => setCheckingSession(false));
  }, []);
  useEffect(() => { if (authenticated) void refreshPolicies(); }, [refreshPolicies, authenticated]);
  useEffect(() => {
    if (!authenticated) return;
    void apiRequest<typeof customers>("/api/v1/customers", actor).then((rows) => { const requested = new URLSearchParams(window.location.search).get("customer"); setCustomers(rows); setSelectedCustomerId((current) => requested && rows.some((row) => row.id === requested) ? requested : current || rows[0]?.id || ""); setInviteCustomerId((current) => current || rows[0]?.id || ""); }).catch((e) => setNotice(e.message));
    void apiRequest<typeof products>("/api/v1/products", actor).then((rows) => { const requested = new URLSearchParams(window.location.search).get("product"); setProducts(rows); if (requested) { const product = rows.find((row) => row.id === requested); if (product) setInquiry((current) => current || `Please quote ${product.id} 1 unit at 0% discount.`); } }).catch((e) => setNotice(e.message));
    if (actorRole === "admin") {
      void apiRequest<WorkspaceMember[]>("/api/v1/auth/members", actor).then(setMembers).catch((e) => setNotice(e.message));
      void apiRequest<WorkspaceInvitation[]>("/api/v1/auth/invitations", actor).then(setInvitations).catch((e) => setNotice(e.message));
    } else { setMembers([]); setInvitations([]); }
    if (actorRole === "manager") void apiRequest<Approval[]>("/api/v1/approvals", actor).then(setApprovals).catch((e) => setNotice(e.message));
    else setApprovals([]);
    if (actorRole === "manager" || actorRole === "admin") void apiRequest<typeof metrics>("/api/v1/metrics", actor).then(setMetrics).catch(() => setMetrics(null));
    else setMetrics(null);
  }, [actor, actorRole, authenticated]);

  function acceptIdentity(user: { actor_id: string; role: string; display_name: string }) {
    setActor(user.actor_id); setActorRole(user.role); setDisplayName(user.display_name); setAuthenticated(true); setPassword(""); setNotice("已安全登录");
  }

  async function signIn(event: FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      const response = await fetch(`${API}/api/v1/auth/login`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: loginId, password }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "邮箱或密码不正确");
      acceptIdentity(payload);
    } catch (error) { setNotice(error instanceof Error ? error.message : "登录失败"); }
    finally { setBusy(false); }
  }

  async function register(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/auth/register`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ organization_name: organizationName, display_name: registerName, email: loginId, password }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "注册失败");
      acceptIdentity(payload);
    } catch (error) { setNotice(error instanceof Error ? error.message : "注册失败"); }
    finally { setBusy(false); }
  }

  async function signOut() {
    await fetch(`${API}/api/v1/auth/logout`, { method: "POST", credentials: "include" });
    setAuthenticated(false); setActor(""); setActorRole(""); setDisplayName(""); setPolicies([]); setCustomers([]); setProducts([]); setMembers([]); setInvitations([]); setInvitationLink(""); setPasswordResetLink(""); setResult(null);
  }

  async function createCustomer(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const customer = await apiRequest<{ id: string; name: string; tier: "VIP" | "Standard" }>("/api/v1/customers", actor, { method: "POST", body: JSON.stringify({ name: newCustomerName, tier: newCustomerTier }) });
      setCustomers((rows) => [...rows, customer]); setSelectedCustomerId(customer.id); setNewCustomerName(""); setNotice("客户已添加到当前工作区");
    } catch (error) { setNotice(error instanceof Error ? error.message : "添加客户失败"); }
    finally { setBusy(false); }
  }

  async function createProduct(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const product = await apiRequest<{ id: string; name: string; currency: string; unit_price: string; stock: number }>("/api/v1/products", actor, { method: "POST", body: JSON.stringify({ ...newProduct, unit_price: newProduct.unit_price, stock: Number(newProduct.stock) }) });
      setProducts((rows) => [...rows, product].sort((a, b) => a.id.localeCompare(b.id))); setInquiry((current) => current || `Please quote ${product.id} 1 unit at 0% discount.`); setNewProduct({ id: "", name: "", currency: "USD", unit_price: "", stock: "" }); setNotice("商品已添加到当前工作区");
    } catch (error) { setNotice(error instanceof Error ? error.message : "添加商品失败"); }
    finally { setBusy(false); }
  }

  async function inviteMember(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(""); setInvitationLink("");
    try {
      const invitation = await apiRequest<{ id: string; token: string }>("/api/v1/auth/invitations", actor, { method: "POST", body: JSON.stringify({ email: inviteEmail, role: inviteRole, customer_id: inviteRole === "sales" ? inviteCustomerId : null }) });
      setInvitationLink(`${window.location.origin}/join?token=${encodeURIComponent(invitation.token)}`);
      setInviteEmail(""); setInvitations(await apiRequest<WorkspaceInvitation[]>("/api/v1/auth/invitations", actor)); setNotice("邀请已创建；请通过安全渠道将链接发送给受邀邮箱对应的员工");
    } catch (error) { setNotice(error instanceof Error ? error.message : "创建邀请失败"); }
    finally { setBusy(false); }
  }

  async function updateMemberStatus(member: WorkspaceMember, isActive: boolean) {
    setBusy(true); setNotice("");
    try {
      await apiRequest(`/api/v1/auth/members/${member.actor_id}`, actor, { method: "PATCH", body: JSON.stringify({ is_active: isActive }) });
      setMembers((current) => current.map((item) => item.actor_id === member.actor_id ? { ...item, is_active: isActive } : item));
      setNotice(isActive ? "成员账号已启用" : "成员已停用，其现有会话已撤销");
    } catch (error) { setNotice(error instanceof Error ? error.message : "更新成员状态失败"); }
    finally { setBusy(false); }
  }

  async function createPasswordResetLink(member: WorkspaceMember) {
    setBusy(true); setNotice(""); setPasswordResetLink("");
    try {
      const reset = await apiRequest<{ token: string }>(`/api/v1/auth/members/${member.actor_id}/password-reset`, actor, { method: "POST" });
      setPasswordResetLink(`${window.location.origin}/reset-password?token=${encodeURIComponent(reset.token)}`);
      setNotice(`已为 ${member.email ?? member.display_name} 生成 1 小时有效的一次性重置链接；请通过安全渠道发送`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "生成密码重置链接失败"); }
    finally { setBusy(false); }
  }

  async function copyPasswordResetLink() {
    try { await navigator.clipboard.writeText(passwordResetLink); setNotice("密码重置链接已复制"); }
    catch { setNotice("复制失败，请检查浏览器剪贴板权限"); }
  }

  async function revokeInvitation(invitation: WorkspaceInvitation) {
    setBusy(true); setNotice("");
    try {
      await apiRequest(`/api/v1/auth/invitations/${invitation.id}`, actor, { method: "DELETE" });
      setInvitations((current) => current.filter((item) => item.id !== invitation.id)); setNotice("邀请已撤销");
    } catch (error) { setNotice(error instanceof Error ? error.message : "撤销邀请失败"); }
    finally { setBusy(false); }
  }

  async function copyInvitationLink() {
    try { await navigator.clipboard.writeText(invitationLink); setNotice("邀请链接已复制"); }
    catch { setNotice("复制失败，请检查浏览器剪贴板权限"); }
  }

  async function runInquiry(event?: FormEvent) {
    event?.preventDefault(); setBusy(true); setNotice(""); setResult(null);
    const key = runKeyRef.current?.actor === actor && runKeyRef.current.inquiry === inquiry
      ? runKeyRef.current.key
      : globalThis.crypto.randomUUID();
    runKeyRef.current = { actor, inquiry, key };
    try {
      if (!selectedCustomerId) throw new Error("请先创建并选择客户账户");
      const next = await apiRequest<RunResult>("/api/v1/runs", actor, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ inquiry, customer_id: selectedCustomerId }) });
      setResult(next);
      setSelectedTrace(next.trace.length ? next.trace.length - 1 : null);
      setEvidenceOpen(false);
      runKeyRef.current = null;
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
        method: "POST", ...(approve ? {} : { body: JSON.stringify({ reason: "Rejected by workspace manager" }) }),
      });
      setApprovals((current) => current.filter((item) => item.id !== approval.id));
      if (outcome.quote) {
        setResult((current) => ({ ...(current ?? { run_id: approval.run_id, status: "completed", execution_mode: "demo", policy_evidence: [] }),
          decision: { decision: "ALLOW", reason_code: "APPROVAL_APPROVED", human_message: "经理批准后已重新校验并创建报价草稿。", policy_version: outcome.quote!.policy_version },
          quote: outcome.quote!, trace: outcome.trace }));
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
      setPolicyDraft(created); setDraftActionType(created.action_type ?? "quote_discount"); setDraftPolicyScope(created.policy_scope ?? "general"); setDraftScopeProductId(created.scope_product_id ?? products[0]?.id ?? ""); setDraftCustomerTier(created.customer_tier ?? "VIP"); setNotice("政策文本已保存为草稿；它不会直接成为授权规则");
    } catch (error) { setNotice(error instanceof Error ? error.message : "上传失败"); }
    finally { setBusy(false); }
  }

  async function savePolicyDraft(activate: boolean) {
    if (!policyDraft) return;
    setBusy(true);
    try {
      const body = {
        title: draftTitle, action_type: draftActionType,
        policy_scope: draftActionType === "quote_discount" ? draftPolicyScope : "general",
        scope_product_id: draftActionType === "quote_discount" && draftPolicyScope === "product_specific" ? draftScopeProductId : null,
        customer_tier: draftCustomerTier,
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
      setDiffResult(await apiRequest("/api/v1/policy-diffs", actor, { method: "POST", body: JSON.stringify({ inquiry, customer_id: selectedCustomerId, base_policy_id: diffBase.split("@")[0], base_version: diffBase.split("@")[1], compare_policy_id: diffCompare.split("@")[0], compare_version: diffCompare.split("@")[1] }) }));
    } catch (error) { setNotice(error instanceof Error ? error.message : "版本对比失败"); }
    finally { setBusy(false); }
  }

  async function downloadQuoteCsv(quoteId: string) {
    const response = await fetch(`${API}/api/v1/quotes/${quoteId}/export.csv`, { headers: {}, credentials: "include", cache: "no-store" });
    if (!response.ok) { setNotice("CSV 导出失败或当前身份无权访问"); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = `dealguard-${quoteId}.csv`; link.click(); URL.revokeObjectURL(url);
  }

  async function copyQuoteSummary(quote: NonNullable<RunResult["quote"]>) {
    try {
      await navigator.clipboard.writeText(`${quote.id} · ${new Intl.NumberFormat("en-US", { style: "currency", currency: quote.breakdown.currency }).format(Number(quote.breakdown.total))}`);
      setNotice("报价摘要已复制");
    } catch { setNotice("复制失败，请检查浏览器剪贴板权限"); }
  }

  const selectedTraceEvent = selectedTrace === null ? null : result?.trace[selectedTrace] ?? null;
  const selectedTraceDetails = selectedTraceEvent?.details ?? {};
  const queryExpansionTerms = Array.isArray(selectedTraceDetails.query_expansion_terms)
    ? selectedTraceDetails.query_expansion_terms.filter((term): term is string => typeof term === "string")
    : [];
  const citationRefs = Array.isArray(selectedTraceDetails.citation_refs)
    ? selectedTraceDetails.citation_refs.filter((ref): ref is string => typeof ref === "string")
    : [];
  const retrievalMode = typeof selectedTraceDetails.retrieval_mode === "string" ? selectedTraceDetails.retrieval_mode : "";
  const candidateCount = typeof selectedTraceDetails.candidate_count === "number" ? selectedTraceDetails.candidate_count : null;
  const evidenceLowRelevance = selectedTraceDetails.evidence_low_relevance === true;
  const policyConflict = selectedTraceDetails.policy_conflict === true;

  if (!authenticated) return <main className="dg-app dg-login-shell"><section className="dg-login-card"><div className="dg-brand-mark" aria-hidden="true">D</div><h1>{checkingSession ? "正在检查登录状态…" : authMode === "register" ? "创建公司工作区" : "登录 DealGuard"}</h1>{!checkingSession && <><p>{authMode === "register" ? "注册后将创建独立工作区，你将成为该工作区管理员。" : "使用工作区邮箱和密码登录。"}</p><div className="dg-auth-tabs"><button type="button" onClick={() => { setAuthMode("login"); setNotice(""); }}>登录</button><button type="button" onClick={() => { setAuthMode("register"); setNotice(""); }}>创建工作区</button></div><form onSubmit={authMode === "register" ? register : signIn} className="dg-login-form">{authMode === "register" && <><label>公司名称<input value={organizationName} onChange={(e) => setOrganizationName(e.target.value)} autoComplete="organization" minLength={2} maxLength={160} required /></label><label>管理员姓名<input value={registerName} onChange={(e) => setRegisterName(e.target.value)} autoComplete="name" minLength={2} maxLength={120} required /></label></>}<label>邮箱<input type="email" value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="email" maxLength={254} required /></label><label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={authMode === "register" ? "new-password" : "current-password"} minLength={authMode === "register" ? 12 : 1} maxLength={1024} required />{authMode === "register" && <small>至少 12 个字符</small>}</label><button className="dg-button" disabled={busy}>{busy ? "处理中…" : authMode === "register" ? "创建工作区" : "登录"}</button>{notice && <p role="status">{notice}</p>}</form></>}</section></main>;

  return (
    <main className="dg-app">
      <header className="dg-topbar">
        <div className="dg-topbar-inner">
          <div className="dg-brand">
            <div className="dg-brand-mark" aria-hidden="true">D</div>
            <div><div className="dg-brand-name">DealGuard</div><div className="dg-brand-subtitle">Policy-aware transaction workspace</div></div>
            <span className="dg-demo-tag">安全会话</span>
          </div>
          <nav className="dg-top-nav" aria-label="主导航"><Link href="/runs">运行记录</Link><Link href="/quotes">报价中心</Link><Link href="/customers">客户</Link><Link href="/products">商品</Link>{(actorRole === "manager" || actorRole === "admin") && <Link href="/evaluation">检索评测</Link>}{actorRole === "admin" && <Link href="/audit">政策审计</Link>}</nav>
          <div className="dg-header-actions">
            <span className="dg-actor">{displayName}</span><button onClick={() => void signOut()} className="dg-reset">退出登录</button>
          </div>
        </div>
      </header>

      <div className="dg-main">
        <div className="dg-page-head">
          <div><h1>询盘与报价工作台</h1><p>检索提供决策依据，确定性规则负责授权。所有报价均为草稿，不会提交真实交易。</p></div>
          <div className="dg-current-policy"><span className="dg-policy-dot" /><span>当前政策</span><strong>{activePolicy ? `${activePolicy.policy_id}@${activePolicy.version}` : "载入中"}</strong></div>
        </div>
        {notice && <div role="status" className="dg-notice">{notice}</div>}

        {actorRole === "manager" && <section className="dg-manager" id="approvals">
          <div className="dg-manager-head"><h2>待处理审批 · {approvals.length}</h2><span className="dg-manager-note">批准时会重新检查政策、归属和库存</span></div>
          {approvals.length === 0 ? <p className="dg-help">当前没有待处理申请。</p> : <div className="dg-approval-list">{approvals.map((approval) => <article key={approval.id} className="dg-approval">
            <div><p className="dg-approval-title">{approval.customer_id} · {approval.product_id}</p><p className="dg-approval-detail">{approval.quantity.toLocaleString()} 件 · {approval.discount_pct}% · {approval.policy_id}@{approval.policy_version}</p></div>
            <div className="dg-approval-actions"><button onClick={() => void reviewApproval(approval, true)} disabled={busy} className="dg-button dg-button-small">批准</button><button onClick={() => void reviewApproval(approval, false)} disabled={busy} className="dg-button dg-button-small dg-button-reject">拒绝</button></div>
          </article>)}</div>}
        </section>}

        {(actorRole === "admin" || actorRole === "manager") && metrics && <section className="dg-metrics" aria-label="运行指标">
          <div className="dg-metric"><div className="dg-metric-label">运行总数</div><div className="dg-metric-value">{metrics.run_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">允许 / 审批 / 拒绝</div><div className="dg-metric-value">{metrics.allow_count} / {metrics.review_count} / {metrics.deny_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">耗时 P50 / P95</div><div className="dg-metric-value">{metrics.p50_duration_ms == null ? "未知" : `${metrics.p50_duration_ms} / ${metrics.p95_duration_ms} ms`}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">规则解析 / 模型解析</div><div className="dg-metric-value">{metrics.demo_run_count} / {metrics.llm_run_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">Token 用量</div><div className="dg-metric-value">{metrics.input_tokens == null ? "未提供" : `${metrics.input_tokens} in / ${metrics.output_tokens ?? 0} out`}</div></div>
        </section>}
        {(actorRole === "admin" || actorRole === "manager") && metrics && <section className="dg-outcome-chart" aria-label="Guard 决策分布"><div><strong>决策分布</strong><span>{decisionTotal.toLocaleString()} 次已判定运行</span></div><div className="dg-outcome-stack" role="img" aria-label={`允许 ${metrics.allow_count}，审批 ${metrics.review_count}，拒绝 ${metrics.deny_count}`}><span className="dg-outcome-allow" style={{ width: `${decisionShares.allow}%` }} /><span className="dg-outcome-review" style={{ width: `${decisionShares.review}%` }} /><span className="dg-outcome-deny" style={{ width: `${decisionShares.deny}%` }} /></div><div className="dg-outcome-legend"><span><i className="dg-legend-allow"/>允许 {metrics.allow_count} · {decisionShares.allow.toFixed(0)}%</span><span><i className="dg-legend-review"/>待审批 {metrics.review_count} · {decisionShares.review.toFixed(0)}%</span><span><i className="dg-legend-deny"/>拒绝 {metrics.deny_count} · {decisionShares.deny.toFixed(0)}%</span></div></section>}

        <div className="dg-workspace">
          <section className="dg-panel dg-panel-pad">
            <div className="dg-panel-heading"><div><div className="dg-kicker">01 · Inquiry</div><h2>客户询盘</h2></div><span className="dg-language">English input</span></div>
            <div className="dg-examples">{products.slice(0, 2).map((product) => { const example = `Please quote ${product.id} 10 units at 5% discount.`; return <button type="button" key={product.id} onClick={() => setInquiry(example)} aria-pressed={inquiry === example} className={`dg-example ${inquiry === example ? "dg-example-active" : ""}`}>{product.id} 报价示例</button>; })}</div>
            <form onSubmit={runInquiry} aria-busy={busy}>
              <label htmlFor="customer" className="dg-field-label">客户账户</label>
              <select id="customer" className="dg-control" value={selectedCustomerId} onChange={(event) => setSelectedCustomerId(event.target.value)} required>
                <option value="">选择客户</option>{customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name} · {customer.tier}</option>)}
              </select>
              <label htmlFor="inquiry" className="dg-field-label">询盘内容</label>
              <textarea id="inquiry" value={inquiry} onChange={(event) => setInquiry(event.target.value)} rows={8} maxLength={10_000} className="dg-textarea" placeholder="Tell us the product, quantity and requested discount..." />
              <div className="dg-composer-meta"><span>支持单商品或多商品询价</span><span>{inquiry.length.toLocaleString()} / 10,000</span></div>
              <div className="dg-composer-actions"><button type="submit" disabled={busy || !inquiry.trim()} className="dg-button dg-button-primary">{busy && <span className="dg-spinner" aria-hidden="true" />}{busy ? "正在分析询盘…" : "运行询盘"}</button><button type="button" disabled={busy || !inquiry} onClick={() => { setInquiry(""); setResult(null); }} className="dg-button dg-button-secondary">清空</button></div>
            </form>
            <p className="dg-help">客户等级、商品价格和库存来自当前工作区。询盘中的自报等级不会用于授权。</p>
          </section>

          <section className="dg-panel dg-panel-pad">
              <div className="dg-panel-heading"><div><div className="dg-kicker">02 · Decision</div><h2>执行结果</h2></div>{result && <span className="dg-kicker">{result.execution_mode === "demo" ? "规则解析" : "模型解析"}</span>}</div>
            {!result ? <div className="dg-empty">
              <div className="dg-empty-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M7 17 17 7M8 7h9v9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></div>
              <p className="dg-empty-title">等待运行询盘</p><p className="dg-empty-copy">结构化请求、Guard 判定和报价草稿会显示在这里。</p>
            </div> : <div className="dg-result">
              <div className={`dg-decision dg-decision-${result.decision?.decision ?? "DENY"}`}>
                <div className="dg-decision-main"><span className="dg-decision-mark" aria-hidden="true">{result.decision?.decision === "ALLOW" ? "✓" : result.decision?.decision === "REVIEW" ? "↗" : result.decision?.decision === "NEEDS_INPUT" ? "!" : "×"}</span><strong className="dg-decision-name">{result.decision?.decision ?? result.status}</strong></div>
                <span className="dg-decision-code">{result.decision?.reason_code}</span>
              </div>
              <p className="dg-message">{result.decision?.human_message ?? "工作流未返回决策。"}</p>
              {result.decision?.decision === "DENY" && result.decision.reason_code === "DISCOUNT_LIMIT_EXCEEDED" && result.decision.suggested_discount_pct != null && result.decision.suggested_product_id && result.intent && <button type="button" className="dg-button dg-button-secondary" onClick={() => { const revised = inquiryWithSuggestedDiscount(result.intent!, result.decision!.suggested_product_id!, result.decision!.suggested_discount_pct!); if (revised) setInquiry(revised); }}>按政策上限修改询盘（{Number(result.decision.suggested_discount_pct)}%）</button>}
              {result.intent && <div className="dg-intent"><div><div className="dg-intent-label">商品</div><div className="dg-intent-value">{result.intent.items?.length ? result.intent.items.map((item) => item.product_id).join(" · ") : result.intent.product_id ?? "—"}</div></div><div><div className="dg-intent-label">数量</div><div className="dg-intent-value">{result.intent.items?.length ? `${result.intent.items.length} 项` : result.intent.quantity?.toLocaleString() ?? "—"}</div></div><div><div className="dg-intent-label">折扣</div><div className="dg-intent-value">{result.intent.items?.length ? result.intent.items.map((item) => `${item.product_id} ${item.requested_discount_pct}%`).join(" · ") : result.intent.requested_discount_pct ? `${result.intent.requested_discount_pct}%` : "—"}</div></div><div><div className="dg-intent-label">政策范围</div><div className="dg-intent-value">{result.intent.policy_scope ? policyScopeLabels[result.intent.policy_scope] : "需澄清"}</div></div></div>}
              {result.quote && <div className="dg-quote">
                <div className="dg-quote-top"><div><div className="dg-quote-label">Quote draft · {result.quote.breakdown.currency}</div><div className="dg-quote-total">{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote.breakdown.currency }).format(Number(result.quote.breakdown.total))}</div><div className="dg-quote-meta">{result.quote.breakdown.quantity.toLocaleString()} units · {result.quote.breakdown.discount_pct}% discount</div></div><div className="dg-quote-policy">政策版本<strong>{result.quote.policy_id}@{result.quote.policy_version}</strong></div></div>
                {result.quote.lines && result.quote.lines.length > 1 && <div className="dg-quote-lines">
                  <div className="dg-table-scroll"><table className="dg-line-table"><thead><tr><th>商品</th><th>数量</th><th>库存校验快照</th><th>单价</th><th>折扣</th><th>金额</th></tr></thead><tbody>{result.quote.lines.map((line) => <tr key={line.product_id}><td><strong>{line.product_name}</strong><span>{line.product_id}</span><details className="dg-line-policy"><summary>{line.policy_id}@{line.policy_version} · 查看溯源</summary>{line.citation_ref && <p>{line.citation_ref}</p>}{line.citation && <p>{line.citation}</p>}<p>来源 SHA-256：{line.policy_source_hash ?? "旧记录无哈希"}</p>{line.authorization_rule && <p>授权规则：最高折扣 {String(line.authorization_rule.max_discount_pct ?? "—")}%；审批上限 {String(line.authorization_rule.review_max_discount_pct ?? "—")}%。</p>}{line.guard_evaluation && <p>Guard：{line.guard_evaluation.decision} · {line.guard_evaluation.reason_code}{line.guard_evaluation.checks?.map((check) => ` · ${check.check}=${check.result}`)}</p>}</details></td><td>{line.quantity.toLocaleString()}</td><td>{line.available_stock == null ? "旧记录无快照" : <>{line.available_stock.toLocaleString()} 可用<br/><small>{line.inventory_checked_at ? new Date(line.inventory_checked_at).toLocaleString() : "校验时间未知"}</small></>}</td><td>{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote!.breakdown.currency }).format(Number(line.unit_price))}</td><td>{line.discount_pct}%</td><td className="dg-line-total">{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote!.breakdown.currency }).format(Number(line.total))}</td></tr>)}</tbody></table></div>
                  <div className="dg-line-chart" aria-label="各商品金额对比"><div className="dg-chart-heading"><span>明细金额对比</span><span>{result.quote.breakdown.currency}</span></div>{result.quote.lines.map((line) => { const amount = Number(line.total); const max = Math.max(...result.quote!.lines!.map((item) => Number(item.total)), 1); return <div className="dg-chart-row" key={line.product_id}><span>{line.product_id}</span><div className="dg-chart-track"><div className="dg-chart-fill" style={{ width: `${Math.max(amount / max * 100, 2)}%` }} /></div><strong>{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote!.breakdown.currency }).format(amount)}</strong></div>; })}</div>
                </div>}
                {result.quote.lines?.length === 1 && <details className="dg-line-policy"><summary>{result.quote.lines[0].policy_id}@{result.quote.lines[0].policy_version} · 查看规则溯源</summary>{result.quote.lines[0].citation_ref && <p>{result.quote.lines[0].citation_ref}</p>}{result.quote.lines[0].citation && <p>{result.quote.lines[0].citation}</p>}<p>来源 SHA-256：{result.quote.lines[0].policy_source_hash ?? "旧记录无哈希"}</p><p>Guard：{result.quote.lines[0].guard_evaluation?.decision} · {result.quote.lines[0].guard_evaluation?.reason_code}{result.quote.lines[0].guard_evaluation?.checks?.map((check) => ` · ${check.check}=${check.result}`)}</p></details>}
                <div className="dg-quote-footer"><span className="dg-quote-id">{result.quote.id}</span><div className="dg-quote-actions"><Link href="/quotes" className="dg-button dg-button-small dg-button-secondary">报价中心</Link><button onClick={() => void copyQuoteSummary(result.quote!)} className="dg-button dg-button-small dg-button-secondary">复制摘要</button><button onClick={() => void downloadQuoteCsv(result.quote!.id)} className="dg-button dg-button-small dg-button-secondary">导出 CSV</button></div></div>
              </div>}
              {result.approval_request && <div className="dg-block"><p className="dg-block-title">经理审批</p><div className="dg-policy-row dg-policy-row-active"><div className="dg-policy-row-top"><strong className="dg-policy-id">{result.approval_request.policy_id}@{result.approval_request.policy_version}</strong><span className="dg-status">待处理</span></div><p className="dg-policy-limit">审批请求已保存；批准前不会生成报价草稿。</p></div></div>}
              <div className="dg-block"><details className="dg-evidence-disclosure" open={evidenceOpen} onToggle={(event) => setEvidenceOpen(event.currentTarget.open)}><summary><span>政策证据</span><span>{result.policy_evidence.length} 条 · {evidenceOpen ? "收起" : "展开"}</span></summary>{result.policy_evidence.length ? result.policy_evidence.map((evidence) => <div key={evidence.citation_ref} className="dg-evidence"><div className="dg-evidence-head"><strong className="dg-evidence-title">{evidence.title}</strong><span className="dg-evidence-source">{evidence.citation_ref}</span></div><p className="dg-evidence-copy">{evidence.citation}</p><div className="dg-evidence-mode">{evidence.source} · {evidence.retrieval_mode}</div></div>) : <p className="dg-help">本次没有适用的政策依据</p>}</details></div>
            </div>}
          </section>

          <aside className="dg-side dg-column">
            {actorRole === "admin" && <section className="dg-panel dg-panel-pad"><div className="dg-panel-heading"><div><div className="dg-kicker">Workspace setup</div><h2>工作区数据</h2></div></div>
              <form onSubmit={createCustomer} className="dg-upload"><h3>添加客户账户</h3><label>客户名称<input value={newCustomerName} onChange={(event) => setNewCustomerName(event.target.value)} className="dg-control" minLength={2} required /></label><label>客户等级<select value={newCustomerTier} onChange={(event) => setNewCustomerTier(event.target.value as "VIP" | "Standard")} className="dg-control"><option value="Standard">Standard</option><option value="VIP">VIP</option></select></label><button className="dg-button dg-button-small dg-button-secondary" disabled={busy}>添加客户</button></form>
              <form onSubmit={createProduct} className="dg-upload"><h3>添加商品</h3><label>商品 ID<input value={newProduct.id} onChange={(event) => setNewProduct({ ...newProduct, id: event.target.value.toUpperCase() })} className="dg-control" pattern="[A-Za-z0-9_-]+" minLength={2} required /></label><label>商品名称<input value={newProduct.name} onChange={(event) => setNewProduct({ ...newProduct, name: event.target.value })} className="dg-control" minLength={2} required /></label><div className="dg-two-fields"><label>币种<input value={newProduct.currency} onChange={(event) => setNewProduct({ ...newProduct, currency: event.target.value.toUpperCase() })} className="dg-control" pattern="[A-Z]{3}" required /></label><label>单价<input type="number" min="0.01" step="0.01" value={newProduct.unit_price} onChange={(event) => setNewProduct({ ...newProduct, unit_price: event.target.value })} className="dg-control" required /></label></div><label>库存<input type="number" min="0" step="1" value={newProduct.stock} onChange={(event) => setNewProduct({ ...newProduct, stock: event.target.value })} className="dg-control" required /></label><button className="dg-button dg-button-small dg-button-secondary" disabled={busy}>添加商品</button></form>
              <form onSubmit={inviteMember} className="dg-upload"><h3>邀请团队成员</h3><label>员工邮箱<input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} className="dg-control" maxLength={254} required /></label><label>工作区角色<select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as "sales" | "manager")} className="dg-control"><option value="sales">销售</option><option value="manager">经理</option></select></label>{inviteRole === "sales" && <label>负责客户<select value={inviteCustomerId} onChange={(event) => setInviteCustomerId(event.target.value)} className="dg-control" required><option value="">选择客户</option>{customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}</select></label>}<button className="dg-button dg-button-small dg-button-secondary" disabled={busy || (inviteRole === "sales" && !customers.length)}>生成 48 小时邀请链接</button></form>
              {invitationLink && <div className="dg-upload"><label>邀请链接<input readOnly value={invitationLink} className="dg-control"/></label><button type="button" onClick={() => void copyInvitationLink()} className="dg-button dg-button-small dg-button-secondary">复制邀请链接</button></div>}
              {passwordResetLink && <div className="dg-upload"><label>密码重置链接<input readOnly value={passwordResetLink} className="dg-control"/></label><button type="button" onClick={() => void copyPasswordResetLink()} className="dg-button dg-button-small dg-button-secondary">复制重置链接</button></div>}
              <div className="dg-upload"><h3>团队成员 · {members.length}</h3>{members.map((member) => <div key={member.actor_id} className="dg-policy-row"><div className="dg-policy-row-top"><strong className="dg-policy-id">{member.display_name}</strong><span className="dg-status">{member.is_active ? "启用" : "已停用"}</span></div><p className="dg-policy-limit">{member.email} · {member.role === "admin" ? "管理员" : member.role === "manager" ? "经理" : "销售"}{member.customer_id ? ` · ${customers.find((customer) => customer.id === member.customer_id)?.name ?? member.customer_id}` : ""}</p><div className="dg-quote-actions"><button type="button" onClick={() => void createPasswordResetLink(member)} disabled={busy || !member.is_active} className="dg-button dg-button-small dg-button-secondary">重置密码</button>{member.actor_id !== actor && <button type="button" onClick={() => void updateMemberStatus(member, !member.is_active)} disabled={busy} className="dg-button dg-button-small dg-button-secondary">{member.is_active ? "停用账号" : "重新启用"}</button>}</div></div>)}</div>
              {invitations.length > 0 && <div className="dg-upload"><h3>待接受邀请 · {invitations.length}</h3>{invitations.map((invitation) => <div key={invitation.id} className="dg-policy-row"><div className="dg-policy-row-top"><strong className="dg-policy-id">{invitation.email}</strong><span className="dg-status">{invitation.expired ? "已过期" : "待接受"}</span></div><p className="dg-policy-limit">{invitation.role === "manager" ? "经理" : "销售"} · 到期 {new Date(invitation.expires_at).toLocaleString()}</p><button type="button" onClick={() => void revokeInvitation(invitation)} disabled={busy} className="dg-button dg-button-small dg-button-secondary">撤销邀请</button></div>)}</div>}
              <p className="dg-help">当前客户：{customers.length} · 当前商品：{products.length}</p>
            </section>}
            <section className="dg-panel dg-panel-pad" id="policy-center">
              <div className="dg-panel-heading"><div><div className="dg-kicker">03 · Policy center</div><h2>政策版本</h2></div></div>
              <div className="dg-policy-list">{policies.map((policy) => <article key={policy.id} className={`dg-policy-row ${policy.status === "active" ? "dg-policy-row-active" : ""}`}>
                <div className="dg-policy-row-top"><strong className="dg-policy-id">{policy.policy_id}@{policy.version}</strong><span className={`dg-status ${policy.status === "active" ? "dg-status-active" : ""}`}>{policy.status}</span></div>
                <p className="dg-policy-limit">{policy.action_type === "quote_discount" ? <>{policyScopeLabels[policy.policy_scope]}{policy.scope_product_id ? ` · ${policy.scope_product_id}` : ""} · 直接上限 <strong>{policy.max_discount_pct}%</strong>{policy.review_max_discount_pct && <> · 审批上限 <strong>{policy.review_max_discount_pct}%</strong></>}</> : "仅供检索参考，不授权报价"}</p>
                {actorRole === "admin" && policy.status !== "active" && <div className="dg-policy-actions"><button onClick={() => activatePolicy(policy)} disabled={busy} className="dg-button dg-button-small dg-button-secondary">激活此版本</button></div>}
              </article>)}</div>
              {actorRole !== "admin" && <p className="dg-policy-only-admin">仅工作区管理员可以管理政策版本。</p>}

              {actorRole === "admin" && <form onSubmit={uploadPolicyDraft} className="dg-upload"><h3>上传政策草稿</h3><p className="dg-help">UTF-8 Markdown / TXT，最大 1 MiB。上传内容不会直接成为授权规则。</p>
                <div className="dg-two-fields"><input aria-label="政策编号" value={draftPolicyId} onChange={(event) => setDraftPolicyId(event.target.value)} className="dg-control"/><input aria-label="政策版本" value={draftVersion} onChange={(event) => setDraftVersion(event.target.value)} className="dg-control"/></div>
                <input aria-label="政策文本文件" type="file" accept=".md,.txt,text/plain" onChange={(event) => setDraftFile(event.target.files?.[0] ?? null)} />
                <button disabled={busy} className="dg-button dg-button-small dg-button-secondary">保存来源草稿</button>
              </form>}
              {actorRole === "admin" && policyDraft && <div className="dg-upload"><h3>{policyDraft.policy_id}@{policyDraft.version} · {policyDraft.status}</h3>
                <div className="dg-rule-fields"><label>政策标题<input aria-label="政策标题" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} className="dg-control"/></label><label>授权范围<select aria-label="政策动作范围" value={draftActionType} onChange={(event) => setDraftActionType(event.target.value as "quote_discount" | "reference_only")} className="dg-control"><option value="quote_discount">折扣报价规则</option><option value="reference_only">仅供检索参考</option></select></label>{draftActionType === "quote_discount" && <><label>适用客户等级<select aria-label="政策客户等级" value={draftCustomerTier} onChange={(event) => setDraftCustomerTier(event.target.value as "VIP" | "Standard")} className="dg-control"><option value="VIP">VIP</option><option value="Standard">Standard</option></select></label><label>适用政策类型<select aria-label="政策适用类型" value={draftPolicyScope} onChange={(event) => setDraftPolicyScope(event.target.value as PolicyScope)} className="dg-control">{Object.entries(policyScopeLabels).map(([scope, label]) => <option key={scope} value={scope}>{label}</option>)}</select></label>{draftPolicyScope === "product_specific" && <label>适用商品 ID<input aria-label="适用商品 ID" value={draftScopeProductId} onChange={(event) => setDraftScopeProductId(event.target.value.trim().toUpperCase())} className="dg-control"/></label>}<label>直接折扣上限 %<input type="number" min="0" max="100" value={directLimit} onChange={(event) => setDirectLimit(event.target.value)} className="dg-control"/></label><label className="dg-checkbox"><input type="checkbox" checked={exceptionEnabled} onChange={(event) => setExceptionEnabled(event.target.checked)}/>允许例外审批</label>{exceptionEnabled && <label>审批折扣上限 %<input type="number" min="0" max="100" value={reviewLimit} onChange={(event) => setReviewLimit(event.target.value)} className="dg-control"/></label>}</>}</div>
                <div className="dg-draft-actions"><button onClick={() => void savePolicyDraft(false)} disabled={busy || policyDraft.status === "active"} className="dg-button dg-button-small dg-button-secondary">保存规则</button><button onClick={() => void savePolicyDraft(true)} disabled={busy || policyDraft.status === "active"} className="dg-button dg-button-small">保存并激活</button></div>
              </div>}

              <div className="dg-divider"><h3 className="dg-block-title">策略版本 Diff</h3><p className="dg-diff-copy">使用当前选定客户和询盘，逐项比较 Guard 结果，不创建报价。</p>
                <div className="dg-diff-controls">{[diffBase, diffCompare].map((value, index) => <select key={index} aria-label={index === 0 ? "基准政策" : "对比政策"} value={value} onChange={(event) => index === 0 ? setDiffBase(event.target.value) : setDiffCompare(event.target.value)} className="dg-control"><option value="">选择版本</option>{policies.map((policy) => <option key={policy.id} value={`${policy.policy_id}@${policy.version}`}>{policy.policy_id}@{policy.version}</option>)}</select>)}</div>
                <button onClick={comparePolicies} disabled={busy || !diffBase || !diffCompare} className="dg-button dg-button-small dg-button-secondary" style={{ width: "100%", marginTop: 8 }}>比较当前询盘</button>
                {diffResult && <div className="dg-diff-result">{[diffResult.base, diffResult.compare].map((side) => <div key={side.version} className="dg-diff-side"><strong>{side.version}: {side.decision.decision}</strong><p>{side.decision.reason_code}</p>{side.breakdown && <p>${Number(side.breakdown.total).toLocaleString()}</p>}{side.lines?.map((line) => <p key={line.product_id}>{line.product_id}: {line.decision.decision} · {line.decision.reason_code}{line.breakdown ? ` · $${Number(line.breakdown.total).toLocaleString()}` : ""}</p>)}</div>)}</div>}
              </div>
            </section>

          </aside>
        </div>
        <section className="dg-panel dg-panel-pad dg-trace-panel"><div className="dg-panel-heading"><div><div className="dg-kicker">04 · Trace</div><h2>运行轨迹</h2></div>{result?.duration_ms != null && <span className="dg-trace-total">{result.duration_ms} ms 总耗时</span>}</div>
          {!result?.trace.length ? <p className="dg-help">运行后的程序事件会显示在这里。</p> : <><ol className="dg-trace dg-trace-interactive" aria-label="工作流阶段">{result.trace.map((event, index) => <li key={`${event.step}-${index}`} className={`dg-trace-item ${selectedTrace === index ? "dg-trace-selected" : ""}`}><button type="button" className="dg-trace-button" aria-expanded={selectedTrace === index} onClick={() => setSelectedTrace(selectedTrace === index ? null : index)}><span className="dg-trace-dot">{index + 1}</span><span className="dg-trace-content"><span className="dg-trace-top"><strong className="dg-trace-step">{traceLabels[event.step] ?? event.step.replaceAll("_", " ")}</strong><span className="dg-trace-status">{event.status}</span></span>{typeof event.details?.duration_ms === "number" && <span className="dg-trace-duration">{event.details.duration_ms} ms</span>}</span></button></li>)}</ol><p className="dg-trace-hint">点选步骤查看事件详情 · 横向滑动查看完整轨迹</p>{selectedTraceEvent && <div className="dg-trace-detail"><div><strong>{traceLabels[selectedTraceEvent.step] ?? selectedTraceEvent.step.replaceAll("_", " ")}</strong><span>{selectedTraceEvent.timestamp ? new Date(selectedTraceEvent.timestamp).toLocaleTimeString() : ""}</span></div><p>{selectedTraceEvent.message}</p>{selectedTraceEvent.step === "retrieve_policy" && <div className="dg-trace-readout"><div><span>检索路径</span><strong>{retrievalModeLabels[retrievalMode] ?? (retrievalMode || "未选择")}</strong></div>{candidateCount !== null && <div><span>有效政策候选</span><strong>{candidateCount}</strong></div>}{queryExpansionTerms.length > 0 && <div><span>命中领域词</span><span className="dg-trace-chips">{queryExpansionTerms.map((term) => <code key={term}>{term}</code>)}</span></div>}{citationRefs.length > 0 && <div><span>原文引用</span><span className="dg-trace-chips">{citationRefs.map((ref) => <code key={ref}>{ref}</code>)}</span></div>}{evidenceLowRelevance && <p className="dg-trace-warning" role="status">没有找到与询盘匹配的政策文本，系统已阻止报价。</p>}{policyConflict && <p className="dg-trace-warning" role="status">有效政策规则存在冲突，系统已阻止报价。</p>}{retrievalMode === "registry_abstain" && !evidenceLowRelevance && !policyConflict && <p className="dg-trace-warning" role="status">多条有效政策同时符合，系统没有自动选择。</p>}</div>}{Object.keys(selectedTraceEvent.details ?? {}).length > 0 && <details className="dg-trace-raw"><summary>查看完整 Trace 字段</summary><pre>{JSON.stringify(selectedTraceEvent.details, null, 2)}</pre></details>}</div>}</>}
        </section>
        <footer className="dg-footer"><p>当前工作区业务数据 · USD</p><p>{result ? `Run ${result.run_id} · ${result.execution_mode === "demo" ? "Rules engine" : "Configured model"}` : "Retrieval informs decisions; deterministic policies authorize actions."}</p></footer>
      </div>
    </main>
  );
}
