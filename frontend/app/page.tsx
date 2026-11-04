"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { BrandMark } from "../components/BrandMark";
import { WorkspaceNav } from "../components/WorkspaceNav";
import { LanguageToggle, localizedDecision, localizedDecisionMessage, localizedReason, localizedStatus, useLanguage } from "../components/LanguageProvider";
import "./workspace.css";

type Actor = string;
type PolicyScope = "general" | "bulk_order" | "loyalty" | "early_payment" | "seasonal_campaign" | "product_specific";
type Policy = {
  id: string; policy_id: string; version: string; title: string; action_type: "quote_discount" | "reference_only"; status: "active" | "inactive";
  policy_scope: PolicyScope; scope_product_id: string | null;
  max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean; source: string;
};
type Approval = { id: string; run_id: string; applicant_actor_id: string; customer_id: string; product_id: string; quantity: number; discount_pct: string; policy_id: string; policy_version: string; state: string };
type PolicyDraft = { id: string; policy_id: string; version: string; title: string; source?: string; action_type: "quote_discount" | "reference_only" | null; policy_scope: PolicyScope; scope_product_id: string | null; customer_tier: "VIP" | "Standard"; max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean; status: string };
type PolicyExtraction = { filename: string; source: string; fields: { title: string | null; action_type: "quote_discount" | "reference_only" | null; policy_scope: PolicyScope | null; scope_product_id: string | null; customer_tier: "VIP" | "Standard" | null; max_discount_pct: string | null; review_max_discount_pct: string | null; exception_approval_allowed: boolean | null; supporting_evidence: string[]; uncertain_fields: string[] } };
type PolicyImportPreview = { filename: string; total_rows: number; valid_rows: number; invalid_rows: number; rows: { row_number: number; policy_id: string | null; version: string | null; title: string | null; action_type: string | null; policy_scope: string; customer_tier: string; errors: string[] }[] };
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
const policyScopeLabels: Record<PolicyScope, { zh: string; en: string }> = {
  general: { zh: "通用折扣", en: "General discount" }, bulk_order: { zh: "批量采购", en: "Bulk order" },
  loyalty: { zh: "忠诚客户", en: "Loyalty" }, early_payment: { zh: "提前付款", en: "Early payment" },
  seasonal_campaign: { zh: "季节活动", en: "Seasonal campaign" }, product_specific: { zh: "指定商品", en: "Product-specific" },
};
const traceLabels: Record<string, { zh: string; en: string }> = {
  parse_intent: { zh: "解析询盘", en: "Parse inquiry" }, load_business_data: { zh: "读取业务数据", en: "Business data" },
  retrieve_policy: { zh: "检索政策", en: "Retrieve policy" }, validate_proposal: { zh: "Guard 校验", en: "Guard validation" },
  create_quote_draft: { zh: "创建报价草稿", en: "Create draft" }, request_approval: { zh: "请求经理审批", en: "Manager review" },
};
const retrievalModeLabels: Record<string, { zh: string; en: string }> = {
  registry: { zh: "政策 Registry", en: "Policy registry" },
  registry_abstain: { zh: "Registry 安全弃权", en: "Registry safe abstention" },
  conflict_graph_abstain: { zh: "政策冲突拦截", en: "Policy conflict block" },
};
const getLabel = (value: { zh: string; en: string }, locale: "zh" | "en") => value[locale];

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
  const { locale, t } = useLanguage();
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
  const [customers, setCustomers] = useState<{ id: string; name: string; tier: "VIP" | "Standard"; image_id: string | null }[]>([]);
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [products, setProducts] = useState<{ id: string; name: string; currency: string; unit_price: string; stock: number; image_id: string | null }[]>([]);
  const [result, setResult] = useState<RunResult | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<number | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [policyDraft, setPolicyDraft] = useState<PolicyDraft | null>(null);
  const [policyDrafts, setPolicyDrafts] = useState<PolicyDraft[]>([]);
  const [draftFile, setDraftFile] = useState<File | null>(null);
  const [policyExtraction, setPolicyExtraction] = useState<PolicyExtraction | null>(null);
  const [extractionReviewed, setExtractionReviewed] = useState(false);
  const [policyImportFile, setPolicyImportFile] = useState<File | null>(null);
  const [policyImportPreview, setPolicyImportPreview] = useState<PolicyImportPreview | null>(null);
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
  const activePolicies = useMemo(() => policies.filter((policy) => policy.status === "active"), [policies]);
  const inactivePolicies = useMemo(() => policies.filter((policy) => policy.status !== "active"), [policies]);
  const selectedCustomer = useMemo(() => customers.find((customer) => customer.id === selectedCustomerId), [customers, selectedCustomerId]);
  const decisionTotal = metrics ? metrics.allow_count + metrics.review_count + metrics.deny_count : 0;
  const decisionShares = metrics && decisionTotal > 0 ? {
    allow: metrics.allow_count / decisionTotal * 100,
    review: metrics.review_count / decisionTotal * 100,
    deny: metrics.deny_count / decisionTotal * 100,
  } : { allow: 0, review: 0, deny: 0 };
  const renderPolicy = (policy: Policy) => <article key={policy.id} className={`dg-policy-row ${policy.status === "active" ? "dg-policy-row-active" : ""}`}>
    <div className="dg-policy-row-top"><strong className="dg-policy-id">{policy.policy_id}@{policy.version}</strong><span className={`dg-status ${policy.status === "active" ? "dg-status-active" : ""}`}>{localizedStatus(policy.status, locale)}</span></div>
    <p className="dg-policy-limit">{policy.action_type === "quote_discount" ? <>{getLabel(policyScopeLabels[policy.policy_scope], locale)}{policy.scope_product_id ? ` · ${policy.scope_product_id}` : ""} · {t("直接上限", "Direct limit")} <strong>{policy.max_discount_pct}%</strong>{policy.review_max_discount_pct && <> · {t("审批上限", "Review limit")} <strong>{policy.review_max_discount_pct}%</strong></>}</> : t("仅供检索参考，不授权报价", "Reference only; does not authorize quoting")}</p>
    {actorRole === "admin" && policy.status !== "active" && <div className="dg-policy-actions"><button onClick={() => activatePolicy(policy)} disabled={busy} className="dg-button dg-button-small dg-button-secondary">{t("激活此版本", "Activate version")}</button></div>}
  </article>;

  const refreshPolicies = useCallback(async () => {
    try { setPolicies(await apiRequest<Policy[]>("/api/v1/policies", actor)); }
    catch (error) { setNotice(error instanceof Error ? error.message : "无法连接到 API"); }
  }, [actor]);
  const refreshPolicyDrafts = useCallback(async () => {
    if (actorRole !== "admin") return;
    try { setPolicyDrafts(await apiRequest<PolicyDraft[]>("/api/v1/policy-drafts", actor)); }
    catch (error) { setNotice(error instanceof Error ? error.message : "无法读取待审核政策草稿"); }
  }, [actor, actorRole]);
  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) return; const user = await response.json(); setActor(user.actor_id); setActorRole(user.role); setDisplayName(user.display_name); setAuthenticated(true); })
      .catch(() => undefined).finally(() => setCheckingSession(false));
  }, []);
  useEffect(() => { if (authenticated) void refreshPolicies(); }, [refreshPolicies, authenticated]);
  useEffect(() => {
    if (!authenticated) return;
    void apiRequest<typeof customers>("/api/v1/customers", actor).then((rows) => { const requested = new URLSearchParams(window.location.search).get("customer"); setCustomers(rows); setSelectedCustomerId((current) => requested && rows.some((row) => row.id === requested) ? requested : current || rows[0]?.id || ""); }).catch((e) => setNotice(e.message));
    void apiRequest<typeof products>("/api/v1/products", actor).then((rows) => { const requested = new URLSearchParams(window.location.search).get("product"); setProducts(rows); if (requested) { const product = rows.find((row) => row.id === requested); if (product) setInquiry((current) => current || `Please quote ${product.id} 1 unit at 0% discount.`); } }).catch((e) => setNotice(e.message));
    if (actorRole === "admin") {
      void refreshPolicyDrafts();
    }
    if (actorRole === "manager") void apiRequest<Approval[]>("/api/v1/approvals", actor).then(setApprovals).catch((e) => setNotice(e.message));
    else setApprovals([]);
    if (actorRole === "manager" || actorRole === "admin") void apiRequest<typeof metrics>("/api/v1/metrics", actor).then(setMetrics).catch(() => setMetrics(null));
    else setMetrics(null);
  }, [actor, actorRole, authenticated, refreshPolicyDrafts]);

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
    setAuthenticated(false); setActor(""); setActorRole(""); setDisplayName(""); setPolicies([]); setCustomers([]); setProducts([]); setResult(null);
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
      setPolicyDraft(created); setPolicyExtraction(null); setExtractionReviewed(false); setDraftTitle(created.title); setDraftActionType(created.action_type ?? "quote_discount"); setDraftPolicyScope(created.policy_scope ?? "general"); setDraftScopeProductId(created.scope_product_id ?? products[0]?.id ?? ""); setDraftCustomerTier(created.customer_tier ?? "VIP"); setDirectLimit(created.max_discount_pct ?? ""); setReviewLimit(created.review_max_discount_pct ?? ""); setExceptionEnabled(created.exception_approval_allowed); setNotice("政策原文已保存为待审核草稿。请配置结构化规则后再激活。"); void refreshPolicyDrafts();
    } catch (error) { setNotice(error instanceof Error ? error.message : "上传失败"); }
    finally { setBusy(false); }
  }

  async function extractPolicyDraft() {
    if (!draftFile) { setNotice("请先选择 .md 或 .txt 政策文件"); return; }
    const form = new FormData(); form.set("policy_id", draftPolicyId); form.set("version", draftVersion); form.set("file", draftFile);
    setBusy(true); setNotice(""); setPolicyExtraction(null); setExtractionReviewed(false); setPolicyDraft(null);
    try {
      const result = await apiRequest<Omit<PolicyExtraction, "source"> & { fields: PolicyExtraction["fields"] }>("/api/v1/policy-drafts/extract", actor, { method: "POST", body: form });
      const source = await draftFile.text();
      setPolicyExtraction({ filename: result.filename, source, fields: result.fields });
      const fields = result.fields;
      const actionType = fields.action_type ?? "quote_discount";
      setDraftTitle(fields.title ?? ""); setDraftActionType(actionType); setDraftPolicyScope(fields.policy_scope ?? "general");
      setDraftScopeProductId(fields.scope_product_id ?? products[0]?.id ?? ""); setDraftCustomerTier(fields.customer_tier ?? "VIP");
      setDirectLimit(fields.max_discount_pct ?? ""); setReviewLimit(fields.review_max_discount_pct ?? "");
      setExceptionEnabled(fields.exception_approval_allowed === true);
      setPolicyDraft({ id: "", policy_id: draftPolicyId, version: draftVersion, title: fields.title ?? "待补充标题", source, action_type: fields.action_type, policy_scope: fields.policy_scope ?? "general", scope_product_id: fields.scope_product_id, customer_tier: fields.customer_tier ?? "VIP", max_discount_pct: fields.max_discount_pct, review_max_discount_pct: fields.review_max_discount_pct, exception_approval_allowed: fields.exception_approval_allowed === true, status: "extracted" });
      setNotice("已生成识别建议。请核对政策原文、补齐不确定字段并确认后，才会保存为待审核草稿。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "政策识别失败"); }
    finally { setBusy(false); }
  }

  async function createReviewedPolicyDraft() {
    if (!policyExtraction || !extractionReviewed) return;
    const isDiscount = draftActionType === "quote_discount";
    if (!draftTitle.trim() || (isDiscount && !directLimit.trim()) || (isDiscount && exceptionEnabled && !reviewLimit.trim()) || (isDiscount && draftPolicyScope === "product_specific" && !draftScopeProductId.trim())) {
      setNotice("请先补齐标题、折扣规则和指定商品等必填信息。"); return;
    }
    setBusy(true); setNotice("");
    try {
      const created = await apiRequest<PolicyDraft>("/api/v1/policy-drafts/confirmed", actor, { method: "POST", body: JSON.stringify({
        policy_id: draftPolicyId.trim(), version: draftVersion.trim(), source: policyExtraction.source, title: draftTitle.trim(),
        action_type: draftActionType, policy_scope: isDiscount ? draftPolicyScope : "general",
        scope_product_id: isDiscount && draftPolicyScope === "product_specific" ? draftScopeProductId.trim() : null,
        customer_tier: draftCustomerTier, max_discount_pct: isDiscount ? directLimit : null,
        review_max_discount_pct: isDiscount && exceptionEnabled ? reviewLimit : null,
        exception_approval_allowed: isDiscount && exceptionEnabled,
      }) });
      setPolicyDraft(created); setPolicyExtraction(null); setExtractionReviewed(false); setDraftFile(null);
      await refreshPolicyDrafts(); setNotice(`${created.policy_id}@${created.version} 已保存为待审核草稿；此操作不会激活政策。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "创建政策草稿失败"); }
    finally { setBusy(false); }
  }

  async function downloadPolicyImportTemplate(format: "csv" | "xlsx") {
    setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/policy-drafts/import/template?format=${format}`, { credentials: "include", cache: "no-store" });
      if (!response.ok) throw new Error("模板下载失败；请确认当前账号是工作区管理员");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `dealguard-policy-template.${format}`; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setNotice(error instanceof Error ? error.message : "模板下载失败"); }
    finally { setBusy(false); }
  }

  async function requestPolicyImport(path: string) {
    if (!policyImportFile) throw new Error("请先选择 CSV 或 XLSX 文件");
    const form = new FormData(); form.set("file", policyImportFile);
    const response = await fetch(`${API}/api/v1/policy-drafts/import${path}`, { method: "POST", credentials: "include", body: form });
    const payload = await response.json();
    if (!response.ok) {
      if (payload?.detail?.preview) setPolicyImportPreview(payload.detail.preview as PolicyImportPreview);
      throw new Error(payload?.detail?.message ?? "政策导入失败");
    }
    return payload;
  }

  async function previewPolicyImport(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(""); setPolicyImportPreview(null);
    try {
      const preview = await requestPolicyImport("/preview") as PolicyImportPreview;
      setPolicyImportPreview(preview); setNotice(`校验完成：${preview.valid_rows} 条可导入，${preview.invalid_rows} 条需要修正。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "政策预览失败"); }
    finally { setBusy(false); }
  }

  async function confirmPolicyImport() {
    setBusy(true); setNotice("");
    try {
      const result = await requestPolicyImport("") as { imported_count: number };
      setPolicyImportFile(null); setPolicyImportPreview(null); await refreshPolicyDrafts();
      setNotice(`已导入 ${result.imported_count} 条待审核政策草稿。政策尚未激活。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "政策导入失败"); }
    finally { setBusy(false); }
  }

  function openPolicyDraft(draft: PolicyDraft) {
    setPolicyDraft(draft); setPolicyExtraction(null); setExtractionReviewed(false); setDraftFile(null);
    setDraftPolicyId(draft.policy_id); setDraftVersion(draft.version); setDraftTitle(draft.title);
    setDraftActionType(draft.action_type ?? "quote_discount"); setDraftPolicyScope(draft.policy_scope ?? "general");
    setDraftScopeProductId(draft.scope_product_id ?? ""); setDraftCustomerTier(draft.customer_tier ?? "VIP");
    setDirectLimit(draft.max_discount_pct ?? ""); setReviewLimit(draft.review_max_discount_pct ?? "");
    setExceptionEnabled(draft.exception_approval_allowed);
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
        setPolicyDraft(active); await refreshPolicies(); await refreshPolicyDrafts(); setNotice(`${active.policy_id}@${active.version} 已激活`);
      } else { await refreshPolicyDrafts(); setNotice("结构化规则已保存为待审草稿"); }
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

  if (!authenticated) return <main className="dg-app dg-login-shell"><section className="dg-login-card"><div className="dg-login-language"><LanguageToggle /></div><div className="dg-brand-mark"><BrandMark /></div><h1>{checkingSession ? t("正在检查登录状态…", "Checking your session…") : authMode === "register" ? t("创建公司工作区", "Create a company workspace") : t("登录 DealGuard", "Sign in to DealGuard")}</h1>{!checkingSession && <><p>{authMode === "register" ? t("注册后将创建独立工作区，你将成为该工作区管理员。", "Registration creates an isolated workspace and makes you its administrator.") : t("使用工作区邮箱和密码登录。", "Sign in with your workspace email and password.")}</p><div className="dg-auth-tabs"><button type="button" onClick={() => { setAuthMode("login"); setNotice(""); }}>{t("登录", "Sign in")}</button><button type="button" onClick={() => { setAuthMode("register"); setNotice(""); }}>{t("创建工作区", "Create workspace")}</button></div><form onSubmit={authMode === "register" ? register : signIn} className="dg-login-form">{authMode === "register" && <><label>{t("公司名称", "Company name")}<input value={organizationName} onChange={(e) => setOrganizationName(e.target.value)} autoComplete="organization" minLength={2} maxLength={160} required /></label><label>{t("管理员姓名", "Administrator name")}<input value={registerName} onChange={(e) => setRegisterName(e.target.value)} autoComplete="name" minLength={2} maxLength={120} required /></label></>}<label>{t("邮箱", "Email")}<input type="email" value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="email" maxLength={254} required /></label><label>{t("密码", "Password")}<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={authMode === "register" ? "new-password" : "current-password"} minLength={authMode === "register" ? 12 : 1} maxLength={1024} required />{authMode === "register" && <small>{t("至少 12 个字符", "At least 12 characters")}</small>}</label><button className="dg-button" disabled={busy}>{busy ? t("处理中…", "Working…") : authMode === "register" ? t("创建工作区", "Create workspace") : t("登录", "Sign in")}</button>{notice && <p role="status">{notice}</p>}</form></>}</section></main>;

  return (
    <main className="dg-app">
      <WorkspaceNav role={actorRole} displayName={displayName} onLogout={signOut} />

      <div className="dg-main">
        <div className="dg-page-head">
          <div className="dg-page-intro"><h1>{t("询盘与报价工作台", "Inquiry & quote workspace")}</h1><p>{t("提交客户询盘，查看分析结果与政策引用。", "Submit customer inquiries and review decisions with policy citations.")}</p></div>
          <div className="dg-page-actions">
            <div className="dg-current-policy"><span className="dg-policy-dot" /><span>{t("当前生效政策", "Active policy")}</span><strong>{activePolicy ? `${activePolicy.policy_id}@${activePolicy.version}` : t("载入中", "Loading")}</strong></div>
            <Link className="dg-page-primary" href="#inquiry-form">{t("新建询盘", "New inquiry")}<span aria-hidden="true">↓</span></Link>
          </div>
        </div>
        {notice && <div role="status" className="dg-notice">{notice}</div>}

        {actorRole === "manager" && <section className="dg-manager" id="approvals">
          <div className="dg-manager-head"><h2>{t("待处理审批", "Pending approvals")} · {approvals.length}</h2><span className="dg-manager-note">{t("批准时会重新检查政策、归属和库存", "Policy, ownership, and inventory are rechecked on approval")}</span></div>
          {approvals.length === 0 ? <p className="dg-help">{t("当前没有待处理申请。", "There are no pending requests.")}</p> : <div className="dg-approval-list">{approvals.map((approval) => <article key={approval.id} className="dg-approval">
            <div><p className="dg-approval-title">{approval.customer_id} · {approval.product_id}</p><p className="dg-approval-detail">{approval.quantity.toLocaleString()} 件 · {approval.discount_pct}% · {approval.policy_id}@{approval.policy_version}</p></div>
            <div className="dg-approval-actions"><button onClick={() => void reviewApproval(approval, true)} disabled={busy} className="dg-button dg-button-small">{t("批准", "Approve")}</button><button onClick={() => void reviewApproval(approval, false)} disabled={busy} className="dg-button dg-button-small dg-button-reject">{t("拒绝", "Reject")}</button></div>
          </article>)}</div>}
        </section>}

        {(actorRole === "admin" || actorRole === "manager") && metrics && <section className="dg-metrics" aria-label="运行指标">
          <div className="dg-metric"><div className="dg-metric-label">{t("运行总数", "Total runs")}</div><div className="dg-metric-value">{metrics.run_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">{t("允许 / 审批 / 拒绝", "Allow / Review / Deny")}</div><div className="dg-metric-value">{metrics.allow_count} / {metrics.review_count} / {metrics.deny_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">{t("耗时 P50 / P95", "Latency P50 / P95")}</div><div className="dg-metric-value">{metrics.p50_duration_ms == null ? t("未知", "N/A") : `${metrics.p50_duration_ms} / ${metrics.p95_duration_ms} ms`}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">{t("规则解析 / 模型解析", "Rules / Model runs")}</div><div className="dg-metric-value">{metrics.demo_run_count} / {metrics.llm_run_count}</div></div>
          <div className="dg-metric"><div className="dg-metric-label">{t("Token 用量", "Token usage")}</div><div className="dg-metric-value">{metrics.input_tokens == null ? t("未提供", "N/A") : `${metrics.input_tokens} in / ${metrics.output_tokens ?? 0} out`}</div></div>
        </section>}
        {(actorRole === "admin" || actorRole === "manager") && metrics && <section className="dg-outcome-chart" aria-label={t("Guard 决策分布", "Guard decision distribution")}><div><strong>{t("决策分布", "Decision distribution")}</strong><span>{decisionTotal.toLocaleString()} {t("次已判定运行", "decided runs")}</span></div><div className="dg-outcome-stack" role="img" aria-label={`${t("允许", "Allow")} ${metrics.allow_count}，${t("审批", "Review")} ${metrics.review_count}，${t("拒绝", "Deny")} ${metrics.deny_count}`}><span className="dg-outcome-allow" style={{ width: `${decisionShares.allow}%` }} /><span className="dg-outcome-review" style={{ width: `${decisionShares.review}%` }} /><span className="dg-outcome-deny" style={{ width: `${decisionShares.deny}%` }} /></div><div className="dg-outcome-legend"><span><i className="dg-legend-allow"/>{t("允许", "Allow")} {metrics.allow_count} · {decisionShares.allow.toFixed(0)}%</span><span><i className="dg-legend-review"/>{t("待审批", "Review")} {metrics.review_count} · {decisionShares.review.toFixed(0)}%</span><span><i className="dg-legend-deny"/>{t("拒绝", "Deny")} {metrics.deny_count} · {decisionShares.deny.toFixed(0)}%</span></div></section>}

        <div className="dg-workspace">
          <section className="dg-panel dg-panel-pad">
            <div className="dg-panel-heading"><div><h2>{t("客户询盘", "Customer inquiry")}</h2></div><span className="dg-language">{t("支持中英文", "Chinese and English supported")}</span></div>
            <div className="dg-examples">{products.slice(0, 2).map((product) => { const example = locale === "zh" ? `请报价 ${product.id}，数量 10 件，折扣 5%。` : `Please quote ${product.id} 10 units at 5% discount.`; return <button type="button" key={product.id} onClick={() => setInquiry(example)} aria-pressed={inquiry === example} className={`dg-example ${inquiry === example ? "dg-example-active" : ""}`}>{product.image_id && <Image src={`${API}/api/v1/media/${encodeURIComponent(product.image_id)}`} width={36} height={36} unoptimized alt="" className="dg-example-image" />}<span><strong>{product.id}</strong><small>{product.name}</small></span></button>; })}</div>
            <form id="inquiry-form" onSubmit={runInquiry} aria-busy={busy}>
              <label htmlFor="customer" className="dg-field-label">{t("客户账户", "Customer account")}</label>
              <select id="customer" className="dg-control" value={selectedCustomerId} onChange={(event) => setSelectedCustomerId(event.target.value)} required>
                <option value="">{t("选择客户", "Select a customer")}</option>{customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name} · {customer.tier}</option>)}
              </select>
              {selectedCustomer?.image_id && <div className="dg-selected-customer"><Image src={`${API}/api/v1/media/${encodeURIComponent(selectedCustomer.image_id)}`} width={36} height={36} unoptimized alt={`${selectedCustomer.name} logo`} /><span><strong>{selectedCustomer.name}</strong><small>{selectedCustomer.tier} · {selectedCustomer.id}</small></span></div>}
              <label htmlFor="inquiry" className="dg-field-label">{t("询盘内容", "Inquiry details")}</label>
              <textarea id="inquiry" value={inquiry} onChange={(event) => setInquiry(event.target.value)} rows={8} maxLength={10_000} className="dg-textarea" placeholder={t("例如：请报价 P-100，数量 100 件，折扣 5%。", "Tell us the product, quantity and requested discount...")} />
              <div className="dg-composer-meta"><span>{t("支持单商品或多商品询价", "Single-item and multi-item inquiries supported")}</span><span>{inquiry.length.toLocaleString()} / 10,000</span></div>
              <div className="dg-composer-actions"><button type="submit" disabled={busy || !inquiry.trim()} className="dg-button dg-button-primary">{busy && <span className="dg-spinner" aria-hidden="true" />}{busy ? t("正在分析询盘…", "Analyzing inquiry…") : t("运行询盘", "Run inquiry")}</button><button type="button" disabled={busy || !inquiry} onClick={() => { setInquiry(""); setResult(null); }} className="dg-button dg-button-secondary">{t("清空", "Clear")}</button></div>
            </form>
            <p className="dg-help">{t("客户等级、商品价格和库存来自当前工作区。询盘中的自报等级不会用于授权。", "Customer tier, product prices, and inventory come from this workspace. Self-reported tiers in inquiries are never used for authorization.")}</p>
          </section>

          <section className="dg-panel dg-panel-pad">
              <div className="dg-panel-heading"><div><h2>{t("执行结果", "Decision result")}</h2></div>{result && <span className={`dg-mode-badge ${result.execution_mode === "demo" ? "dg-mode-demo" : "dg-mode-model"}`}>{result.execution_mode === "demo" ? t("本地规则解析", "Local rules") : t("AI 模型解析", "AI model")}</span>}</div>
            {!result ? <div className="dg-empty">
              <div className="dg-empty-icon" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M7 17 17 7M8 7h9v9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></div>
              <p className="dg-empty-title">{t("等待运行询盘", "Waiting for an inquiry")}</p><p className="dg-empty-copy">{t("结构化请求、Guard 判定和报价草稿会显示在这里。", "Structured intent, Guard decision, and quote draft will appear here.")}</p>
            </div> : <div className="dg-result">
              <div className={`dg-decision dg-decision-${result.decision?.decision ?? "DENY"}`}>
                <div className="dg-decision-main"><span className="dg-decision-mark" aria-hidden="true">{result.decision?.decision === "ALLOW" ? "✓" : result.decision?.decision === "REVIEW" ? "↗" : result.decision?.decision === "NEEDS_INPUT" ? "!" : "×"}</span><strong className="dg-decision-name">{result.decision ? localizedDecision(result.decision.decision, locale) : localizedStatus(result.status, locale)}</strong></div>
                <span className="dg-decision-code">{localizedReason(result.decision?.reason_code, locale)}</span>
              </div>
              <p className="dg-message">{result.decision ? localizedDecisionMessage(result.decision.reason_code, result.decision.human_message, locale) : t("工作流未返回决策。", "The workflow returned no decision.")}</p>
              {result.decision?.decision === "DENY" && result.decision.reason_code === "DISCOUNT_LIMIT_EXCEEDED" && result.decision.suggested_discount_pct != null && result.decision.suggested_product_id && result.intent && <button type="button" className="dg-button dg-button-secondary" onClick={() => { const revised = inquiryWithSuggestedDiscount(result.intent!, result.decision!.suggested_product_id!, result.decision!.suggested_discount_pct!); if (revised) setInquiry(revised); }}>{t("按政策上限修改询盘", "Apply the policy discount limit")}（{Number(result.decision.suggested_discount_pct)}%）</button>}
              {result.intent && <div className="dg-intent"><div><div className="dg-intent-label">{t("商品", "Products")}</div><div className="dg-intent-value">{result.intent.items?.length ? result.intent.items.map((item) => item.product_id).join(" · ") : result.intent.product_id ?? "—"}</div></div><div><div className="dg-intent-label">{t("数量", "Quantity")}</div><div className="dg-intent-value">{result.intent.items?.length ? `${result.intent.items.length} ${t("项", "items")}` : result.intent.quantity?.toLocaleString() ?? "—"}</div></div><div><div className="dg-intent-label">{t("折扣", "Discount")}</div><div className="dg-intent-value">{result.intent.items?.length ? result.intent.items.map((item) => `${item.product_id} ${item.requested_discount_pct}%`).join(" · ") : result.intent.requested_discount_pct ? `${result.intent.requested_discount_pct}%` : "—"}</div></div><div><div className="dg-intent-label">{t("政策范围", "Policy scope")}</div><div className="dg-intent-value">{result.intent.policy_scope ? getLabel(policyScopeLabels[result.intent.policy_scope], locale) : t("需澄清", "Needs clarification")}</div></div></div>}
              {result.quote && <div className="dg-quote">
                <div className="dg-quote-top"><div><div className="dg-quote-label">{t("报价草稿", "Quote draft")} · {result.quote.breakdown.currency}</div><div className="dg-quote-total">{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote.breakdown.currency }).format(Number(result.quote.breakdown.total))}</div><div className="dg-quote-meta">{result.quote.breakdown.quantity.toLocaleString()} {t("件", "units")} · {result.quote.breakdown.discount_pct}% {t("折扣", "discount")}</div></div><div className="dg-quote-policy">{t("政策版本", "Policy version")}<strong>{result.quote.policy_id}@{result.quote.policy_version}</strong></div></div>
                {result.quote.lines && result.quote.lines.length > 1 && <div className="dg-quote-lines">
                  <div className="dg-table-scroll"><table className="dg-line-table"><thead><tr><th>{t("商品", "Product")}</th><th>{t("数量", "Quantity")}</th><th>{t("库存快照", "Stock snapshot")}</th><th>{t("单价", "Unit price")}</th><th>{t("折扣", "Discount")}</th><th>{t("金额", "Total")}</th></tr></thead><tbody>{result.quote.lines.map((line) => <tr key={line.product_id}><td><strong>{line.product_name}</strong><span>{line.product_id}</span><details className="dg-line-policy"><summary>{line.policy_id}@{line.policy_version} · {t("查看溯源", "View source")}</summary>{line.citation_ref && <p>{line.citation_ref}</p>}{line.citation && <p>{line.citation}</p>}<p>SHA-256: {line.policy_source_hash ?? t("旧记录无哈希", "Unavailable for this older record")}</p>{line.authorization_rule && <p>{t("授权规则", "Authorization rule")}: {t("最高折扣", "Max discount")} {String(line.authorization_rule.max_discount_pct ?? "—")}%；{t("审批上限", "Review limit")} {String(line.authorization_rule.review_max_discount_pct ?? "—")}%。</p>}{line.guard_evaluation && <p>Guard: {localizedDecision(line.guard_evaluation.decision, locale)} · {localizedReason(line.guard_evaluation.reason_code, locale)}{line.guard_evaluation.checks?.map((check) => ` · ${check.check}=${check.result}`)}</p>}</details></td><td>{line.quantity.toLocaleString()}</td><td>{line.available_stock == null ? t("旧记录无快照", "No snapshot on older record") : <>{line.available_stock.toLocaleString()} {t("可用", "available")}<br/><small>{line.inventory_checked_at ? new Date(line.inventory_checked_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US") : t("校验时间未知", "Check time unavailable")}</small></>}</td><td>{new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", { style: "currency", currency: result.quote!.breakdown.currency }).format(Number(line.unit_price))}</td><td>{line.discount_pct}%</td><td className="dg-line-total">{new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", { style: "currency", currency: result.quote!.breakdown.currency }).format(Number(line.total))}</td></tr>)}</tbody></table></div>
                  <div className="dg-line-chart" aria-label="各商品金额对比"><div className="dg-chart-heading"><span>明细金额对比</span><span>{result.quote.breakdown.currency}</span></div>{result.quote.lines.map((line) => { const amount = Number(line.total); const max = Math.max(...result.quote!.lines!.map((item) => Number(item.total)), 1); return <div className="dg-chart-row" key={line.product_id}><span>{line.product_id}</span><div className="dg-chart-track"><div className="dg-chart-fill" style={{ width: `${Math.max(amount / max * 100, 2)}%` }} /></div><strong>{new Intl.NumberFormat("en-US", { style: "currency", currency: result.quote!.breakdown.currency }).format(amount)}</strong></div>; })}</div>
                </div>}
                {result.quote.lines?.length === 1 && <details className="dg-line-policy"><summary>{result.quote.lines[0].policy_id}@{result.quote.lines[0].policy_version} · 查看规则溯源</summary>{result.quote.lines[0].citation_ref && <p>{result.quote.lines[0].citation_ref}</p>}{result.quote.lines[0].citation && <p>{result.quote.lines[0].citation}</p>}<p>来源 SHA-256：{result.quote.lines[0].policy_source_hash ?? "旧记录无哈希"}</p><p>Guard：{result.quote.lines[0].guard_evaluation?.decision} · {result.quote.lines[0].guard_evaluation?.reason_code}{result.quote.lines[0].guard_evaluation?.checks?.map((check) => ` · ${check.check}=${check.result}`)}</p></details>}
                <div className="dg-quote-footer"><span className="dg-quote-id">{result.quote.id}</span><div className="dg-quote-actions"><Link href="/quotes" className="dg-button dg-button-small dg-button-secondary">{t("报价中心", "Quotes")}</Link><button onClick={() => void copyQuoteSummary(result.quote!)} className="dg-button dg-button-small dg-button-secondary">{t("复制摘要", "Copy summary")}</button><button onClick={() => void downloadQuoteCsv(result.quote!.id)} className="dg-button dg-button-small dg-button-secondary">{t("导出 CSV", "Export CSV")}</button></div></div>
              </div>}
              {result.approval_request && <div className="dg-block"><p className="dg-block-title">{t("经理审批", "Manager review")}</p><div className="dg-policy-row dg-policy-row-active"><div className="dg-policy-row-top"><strong className="dg-policy-id">{result.approval_request.policy_id}@{result.approval_request.policy_version}</strong><span className="dg-status">{t("待处理", "Pending")}</span></div><p className="dg-policy-limit">{t("审批请求已保存；批准前不会生成报价草稿。", "The review request is saved; no quote draft will be created until approval.")}</p></div></div>}
              <div className="dg-block"><details className="dg-evidence-disclosure" open={evidenceOpen} onToggle={(event) => setEvidenceOpen(event.currentTarget.open)}><summary><span>{t("政策证据", "Policy evidence")}</span><span>{result.policy_evidence.length} {t("条", "items")} · {evidenceOpen ? t("收起", "Collapse") : t("展开", "Expand")}</span></summary>{result.policy_evidence.length ? result.policy_evidence.map((evidence) => <div key={evidence.citation_ref} className="dg-evidence"><div className="dg-evidence-head"><strong className="dg-evidence-title">{evidence.title}</strong><span className="dg-evidence-source">{evidence.citation_ref}</span></div><p className="dg-evidence-copy">{evidence.citation}</p><div className="dg-evidence-mode">{evidence.source} · {evidence.retrieval_mode}</div></div>) : <p className="dg-help">{t("本次没有适用的政策依据", "No applicable policy evidence was found for this inquiry.")}</p>}</details></div>
            </div>}
          </section>

          <aside className="dg-side dg-column">
            <section className="dg-panel dg-panel-pad" id="policy-center">
              <div className="dg-panel-heading"><div><h2>{t("政策版本", "Policy versions")}</h2></div></div>
              <div className="dg-policy-list">{activePolicies.map(renderPolicy)}</div>
              {inactivePolicies.length > 0 && <details className="dg-policy-archive"><summary><span>{t("其他政策版本", "Other policy versions")}</span><small>{t(`${inactivePolicies.length} 个未生效`, `${inactivePolicies.length} inactive`)}</small></summary><div className="dg-policy-list">{inactivePolicies.map(renderPolicy)}</div></details>}
              {actorRole !== "admin" && <p className="dg-policy-only-admin">{t("仅工作区管理员可以管理政策版本。", "Only workspace administrators can manage policy versions.")}</p>}

              {actorRole === "admin" && <>
                <details className="dg-policy-import"><summary>{t("CSV / Excel 批量导入", "Bulk import CSV / Excel")}</summary><p className="dg-policy-import-copy">{t("最多 500 条。必填列包括政策编号、版本、标题、原文、动作类型和审批标记。动作类型填写 quote_discount 或 reference_only；范围填写 general、bulk_order、loyalty、early_payment、seasonal_campaign 或 product_specific；客户等级填写 VIP 或 Standard。导入只会创建待审核草稿，不会激活政策；已有编号和版本不会覆盖。", "Up to 500 rows. Required columns: policy ID, version, title, source, action type, and approval flag. Use quote_discount or reference_only for action type; valid scopes are general, bulk_order, loyalty, early_payment, seasonal_campaign, and product_specific. Import creates review drafts only and never activates or overwrites existing versions.")}</p>
                  <div className="dg-policy-import-buttons"><button type="button" onClick={() => void downloadPolicyImportTemplate("csv")} disabled={busy} className="dg-button dg-button-small dg-button-secondary">{t("下载 CSV 模板", "Download CSV template")}</button><button type="button" onClick={() => void downloadPolicyImportTemplate("xlsx")} disabled={busy} className="dg-button dg-button-small dg-button-secondary">{t("下载 Excel 模板", "Download Excel template")}</button></div>
                  <form className="dg-policy-import-form" onSubmit={previewPolicyImport}><label>{t("选择政策表格", "Select policy spreadsheet")}<input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { setPolicyImportFile(event.target.files?.[0] ?? null); setPolicyImportPreview(null); }} /></label><button disabled={busy || !policyImportFile} className="dg-button dg-button-small dg-button-secondary">{busy ? t("正在校验…", "Validating…") : t("预览并校验", "Preview and validate")}</button></form>
                  {policyImportPreview && <div className="dg-policy-import-preview"><strong>{policyImportPreview.filename}</strong><span>{t(`共 ${policyImportPreview.total_rows} 条 · 可导入 ${policyImportPreview.valid_rows} 条 · 需修正 ${policyImportPreview.invalid_rows} 条`, `${policyImportPreview.total_rows} rows · ${policyImportPreview.valid_rows} valid · ${policyImportPreview.invalid_rows} need correction`)}</span>{policyImportPreview.rows.length > 0 && <div className="dg-policy-import-rows">{policyImportPreview.rows.slice(0, 30).map((row) => <article key={row.row_number}><strong>{t(`第 ${row.row_number} 行`, `Row ${row.row_number}`)} · {row.policy_id ?? t("缺少编号", "Missing ID")}@{row.version ?? t("缺少版本", "Missing version")}</strong><span>{row.title ?? t("缺少标题", "Missing title")} · {row.action_type ?? t("类型待确认", "Action type required")}</span>{row.errors.length ? <small>{row.errors.join(" ")}</small> : <small className="dg-import-ok">{t("可导入为待审核草稿", "Valid review draft")}</small>}</article>)}</div>}{policyImportPreview.rows.length > 30 && <p>{t("列表仅显示前 30 条，提交时会重新校验全部内容。", "Only the first 30 rows are shown. All rows are validated again on submit.")}</p>}{policyImportPreview.invalid_rows > 0 && <p className="dg-import-warning">{t("存在错误时整批不会导入，请修正后重新预览。", "The batch is rejected if any row has errors. Correct the file and preview it again.")}</p>}<button type="button" onClick={() => void confirmPolicyImport()} disabled={busy || !policyImportPreview.valid_rows || !!policyImportPreview.invalid_rows || !policyImportFile} className="dg-button">{t("创建", "Create")} {policyImportPreview.valid_rows} {t("条待审核草稿", "review drafts")}</button></div>}
                </details>

                {policyDrafts.length > 0 && <div className="dg-policy-draft-queue"><h3>待审核草稿 · {policyDrafts.length}</h3>{policyDrafts.map((draft) => <article key={draft.id}><div><strong>{draft.policy_id}@{draft.version}</strong><span>{draft.title} · {draft.action_type === "reference_only" ? "仅供参考" : draft.max_discount_pct == null ? "规则待补齐" : `${draft.max_discount_pct}% 折扣上限`}</span></div><button type="button" className="dg-button dg-button-small dg-button-secondary" onClick={() => openPolicyDraft(draft)}>审核草稿</button></article>)}</div>}

                <form onSubmit={uploadPolicyDraft} className="dg-upload"><h3>{t("新建政策草稿", "Create policy draft")}</h3><p className="dg-help">{t("上传 UTF-8 Markdown / TXT 原文；AI 识别使用当前配置的模型服务，生成建议后仍需管理员核对。", "Upload UTF-8 Markdown or TXT source. AI extraction uses your configured model and returns suggestions that an administrator must review.")}</p>
                  <div className="dg-two-fields"><input aria-label="政策编号" value={draftPolicyId} onChange={(event) => setDraftPolicyId(event.target.value)} className="dg-control" placeholder="政策编号"/><input aria-label="政策版本" value={draftVersion} onChange={(event) => setDraftVersion(event.target.value)} className="dg-control" placeholder="版本号"/></div>
                  <input aria-label="政策文本文件" type="file" accept=".md,.txt,text/plain" onChange={(event) => { setDraftFile(event.target.files?.[0] ?? null); setPolicyExtraction(null); setPolicyDraft(null); setExtractionReviewed(false); }} />
                  <div className="dg-policy-import-buttons"><button type="button" onClick={() => void extractPolicyDraft()} disabled={busy || !draftFile} className="dg-button">{t("AI 识别规则", "Extract rules with AI")}</button><button disabled={busy || !draftFile} className="dg-button dg-button-small dg-button-secondary">{t("仅保存原文草稿", "Save source draft only")}</button></div>
                </form>
                {policyExtraction && <div className="dg-policy-extraction"><h3>模型识别建议 · 请核对</h3><p>模型仅提供字段建议，不作政策解释授权。请对照原文逐项检查并修正下方字段。</p><details open><summary>政策原文与引用依据</summary><pre>{policyExtraction.source}</pre>{policyExtraction.fields.supporting_evidence.length > 0 ? <ul>{policyExtraction.fields.supporting_evidence.map((evidence, index) => <li key={`${index}-${evidence}`}>“{evidence}”</li>)}</ul> : <span>模型未能提供可核对的原文引用。</span>}</details>{policyExtraction.fields.uncertain_fields.length > 0 && <div className="dg-import-warning">需人工补充或确认：{policyExtraction.fields.uncertain_fields.join("、")}</div>}<label className="dg-policy-review-check"><input type="checkbox" checked={extractionReviewed} onChange={(event) => setExtractionReviewed(event.target.checked)}/>我已对照政策原文核对识别结果</label></div>}
              </>}
              {actorRole === "admin" && policyDraft && <div className="dg-upload"><h3>{policyDraft.policy_id}@{policyDraft.version} · {policyDraft.status}</h3>
                <div className="dg-rule-fields"><label>政策标题<input aria-label="政策标题" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} className="dg-control"/></label><label>授权范围<select aria-label="政策动作范围" value={draftActionType} onChange={(event) => setDraftActionType(event.target.value as "quote_discount" | "reference_only")} className="dg-control"><option value="quote_discount">折扣报价规则</option><option value="reference_only">仅供检索参考</option></select></label>{draftActionType === "quote_discount" && <><label>适用客户等级<select aria-label="政策客户等级" value={draftCustomerTier} onChange={(event) => setDraftCustomerTier(event.target.value as "VIP" | "Standard")} className="dg-control"><option value="VIP">VIP</option><option value="Standard">Standard</option></select></label><label>适用政策类型<select aria-label="政策适用类型" value={draftPolicyScope} onChange={(event) => setDraftPolicyScope(event.target.value as PolicyScope)} className="dg-control">{Object.entries(policyScopeLabels).map(([scope, labels]) => <option key={scope} value={scope}>{getLabel(labels, locale)}</option>)}</select></label>{draftPolicyScope === "product_specific" && <label>适用商品 ID<input aria-label="适用商品 ID" value={draftScopeProductId} onChange={(event) => setDraftScopeProductId(event.target.value.trim().toUpperCase())} className="dg-control"/></label>}<label>直接折扣上限 %<input type="number" min="0" max="100" value={directLimit} onChange={(event) => setDirectLimit(event.target.value)} className="dg-control"/></label><label className="dg-checkbox"><input type="checkbox" checked={exceptionEnabled} onChange={(event) => setExceptionEnabled(event.target.checked)}/>{t("允许例外审批", "Allow exception review")}</label>{exceptionEnabled && <label>审批折扣上限 %<input type="number" min="0" max="100" value={reviewLimit} onChange={(event) => setReviewLimit(event.target.value)} className="dg-control"/></label>}</>}</div>
                {policyExtraction && <div className="dg-policy-draft-source">来源文件：{policyExtraction.filename} · 引用原文和字段需管理员确认。</div>}
                <div className="dg-draft-actions">{policyDraft.status === "extracted" ? <button type="button" onClick={() => void createReviewedPolicyDraft()} disabled={busy || !extractionReviewed} className="dg-button">{busy ? "正在保存…" : "确认并保存为待审核草稿"}</button> : <><button type="button" onClick={() => void savePolicyDraft(false)} disabled={busy || policyDraft.status === "active"} className="dg-button dg-button-small dg-button-secondary">保存规则</button><button type="button" onClick={() => void savePolicyDraft(true)} disabled={busy || policyDraft.status === "active"} className="dg-button dg-button-small">保存并激活</button></>}</div>
              </div>}

              <div className="dg-divider"><h3 className="dg-block-title">{t("策略版本对比", "Policy version comparison")}</h3><p className="dg-diff-copy">{t("使用当前选定客户和询盘，逐项比较 Guard 结果，不创建报价。", "Compare Guard results for the selected customer and inquiry without creating a quote.")}</p>
                <div className="dg-diff-controls">{[diffBase, diffCompare].map((value, index) => <select key={index} aria-label={index === 0 ? "基准政策" : "对比政策"} value={value} onChange={(event) => index === 0 ? setDiffBase(event.target.value) : setDiffCompare(event.target.value)} className="dg-control"><option value="">选择版本</option>{policies.map((policy) => <option key={policy.id} value={`${policy.policy_id}@${policy.version}`}>{policy.policy_id}@{policy.version}</option>)}</select>)}</div>
                <button onClick={comparePolicies} disabled={busy || !diffBase || !diffCompare} className="dg-button dg-button-small dg-button-secondary" style={{ width: "100%", marginTop: 8 }}>比较当前询盘</button>
                {diffResult && <div className="dg-diff-result">{[diffResult.base, diffResult.compare].map((side) => <div key={side.version} className="dg-diff-side"><strong>{side.version}: {side.decision.decision}</strong><p>{side.decision.reason_code}</p>{side.breakdown && <p>${Number(side.breakdown.total).toLocaleString()}</p>}{side.lines?.map((line) => <p key={line.product_id}>{line.product_id}: {line.decision.decision} · {line.decision.reason_code}{line.breakdown ? ` · $${Number(line.breakdown.total).toLocaleString()}` : ""}</p>)}</div>)}</div>}
              </div>
            </section>

          </aside>
        </div>
        <section className="dg-panel dg-panel-pad dg-trace-panel"><div className="dg-panel-heading"><div><h2>{t("运行轨迹", "Workflow trace")}</h2></div>{result?.duration_ms != null && <span className="dg-trace-total">{result.duration_ms} ms {t("总耗时", "total")}</span>}</div>
          {!result?.trace.length ? <p className="dg-help">{t("运行后的程序事件会显示在这里。", "Workflow events will appear here after a run.")}</p> : <><ol className="dg-trace dg-trace-interactive" aria-label={t("工作流阶段", "Workflow stages")}>{result.trace.map((event, index) => <li key={`${event.step}-${index}`} className={`dg-trace-item ${selectedTrace === index ? "dg-trace-selected" : ""}`}><button type="button" className="dg-trace-button" aria-expanded={selectedTrace === index} onClick={() => setSelectedTrace(selectedTrace === index ? null : index)}><span className="dg-trace-dot">{index + 1}</span><span className="dg-trace-content"><span className="dg-trace-top"><strong className="dg-trace-step">{traceLabels[event.step] ? getLabel(traceLabels[event.step], locale) : event.step.replaceAll("_", " ")}</strong><span className="dg-trace-status">{localizedStatus(event.status, locale)}</span></span>{typeof event.details?.duration_ms === "number" && <span className="dg-trace-duration">{event.details.duration_ms} ms</span>}</span></button></li>)}</ol><p className="dg-trace-hint">{t("点选步骤查看事件详情 · 横向滑动查看完整轨迹", "Select a stage to inspect its details · Scroll horizontally to view the full trace")}</p>{selectedTraceEvent && <div className="dg-trace-detail"><div><strong>{traceLabels[selectedTraceEvent.step] ? getLabel(traceLabels[selectedTraceEvent.step], locale) : selectedTraceEvent.step.replaceAll("_", " ")}</strong><span>{selectedTraceEvent.timestamp ? new Date(selectedTraceEvent.timestamp).toLocaleTimeString() : ""}</span></div><p>{selectedTraceEvent.message}</p>{selectedTraceEvent.step === "retrieve_policy" && <div className="dg-trace-readout"><div><span>{t("检索路径", "Retrieval path")}</span><strong>{retrievalModeLabels[retrievalMode] ? getLabel(retrievalModeLabels[retrievalMode], locale) : (retrievalMode || "—")}</strong></div>{candidateCount !== null && <div><span>{t("有效政策候选", "Active policy candidates")}</span><strong>{candidateCount}</strong></div>}{queryExpansionTerms.length > 0 && <div><span>{t("命中领域词", "Domain terms")}</span><span className="dg-trace-chips">{queryExpansionTerms.map((term) => <code key={term}>{term}</code>)}</span></div>}{citationRefs.length > 0 && <div><span>{t("原文引用", "Source citations")}</span><span className="dg-trace-chips">{citationRefs.map((ref) => <code key={ref}>{ref}</code>)}</span></div>}{evidenceLowRelevance && <p className="dg-trace-warning" role="status">{t("没有找到与询盘匹配的政策文本，系统已阻止报价。", "No relevant policy text was found; quoting was blocked.")}</p>}{policyConflict && <p className="dg-trace-warning" role="status">{t("有效政策规则存在冲突，系统已阻止报价。", "Active policy rules conflict; quoting was blocked.")}</p>}{retrievalMode === "registry_abstain" && !evidenceLowRelevance && !policyConflict && <p className="dg-trace-warning" role="status">{t("多条有效政策同时符合，系统没有自动选择。", "Multiple active policies matched, so the system did not choose automatically.")}</p>}</div>}{Object.keys(selectedTraceEvent.details ?? {}).length > 0 && <details className="dg-trace-raw"><summary>{t("查看完整 Trace 字段", "View all trace fields")}</summary><pre>{JSON.stringify(selectedTraceEvent.details, null, 2)}</pre></details>}</div>}</>}
        </section>
        <footer className="dg-footer"><p>当前工作区业务数据 · USD</p><p>{result ? `Run ${result.run_id} · ${result.execution_mode === "demo" ? "Rules engine" : "Configured model"}` : "Retrieval informs decisions; deterministic policies authorize actions."}</p></footer>
      </div>
    </main>
  );
}
