"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type Locale = "zh" | "en";

type LanguageContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (zh: string, en: string) => string;
};

const LanguageContext = createContext<LanguageContextValue | null>(null);
const STORAGE_KEY = "dealguard.locale";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>("zh");

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    const initial: Locale = saved === "en" ? "en" : "zh";
    setLocaleState(initial);
    document.documentElement.lang = initial === "zh" ? "zh-CN" : "en";
  }, []);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.lang = next === "zh" ? "zh-CN" : "en";
  }, []);

  const value = useMemo<LanguageContextValue>(() => ({
    locale,
    setLocale,
    t: (zh, en) => locale === "zh" ? zh : en,
  }), [locale, setLocale]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error("useLanguage must be used inside LanguageProvider");
  return context;
}

export function LanguageToggle() {
  const { locale, setLocale, t } = useLanguage();
  return <label className="dg-language-toggle">
    <span>{t("语言", "Language")}</span>
    <select aria-label={t("界面语言", "Interface language")} value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
      <option value="zh">中文</option>
      <option value="en">English</option>
    </select>
  </label>;
}

export function localizedDecision(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({ ALLOW: "允许", REVIEW: "待审批", DENY: "拒绝", NEEDS_INPUT: "需要补充信息" } as Record<string, string>)[value] ?? value;
}

export function localizedStatus(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({
    completed: "已完成", needs_input: "需要补充信息", failed: "失败", running: "运行中",
    SUCCESS: "成功", FAILED: "失败", DENIED: "拒绝", PENDING: "待处理", STALE: "已失效",
    denied: "拒绝", LOW_RELEVANCE: "相关度不足", low_relevance: "相关度不足",
    conflict: "规则冲突", scope_ambiguous: "范围不明确", scope_unmatched: "范围不匹配",
    ambiguous: "存在多个候选", no_policy: "无适用政策", invalid_output_sanitized: "无效输出已安全处理",
    allow: "允许", review: "待审批", no_evidence: "未找到依据",
    DRAFT: "报价草稿", REVISED: "待发布修订", ISSUED: "已发布", ACCEPTED: "客户已接受", REJECTED: "客户已拒绝", EXPIRED: "已过期", SUPERSEDED: "已被新版本替代", QUOTE_ISSUED: "发布报价", QUOTE_EXPIRED: "报价过期", QUOTE_REVISION_CREATED: "创建报价修订", CUSTOMER_ACCEPTED: "客户接受", CUSTOMER_REJECTED: "客户拒绝",
    active: "生效中", inactive: "未生效", draft: "草稿", extracted: "已提取待复核",
    replayed: "已回放", skipped: "已跳过", pending: "待处理", approved: "已批准", rejected: "已拒绝",
  } as Record<string, string>)[value] ?? value;
}

export function localizedExecutionMode(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({ llm: "模型解析", demo: "规则解析", configured_model: "已配置模型", rules_engine: "规则引擎" } as Record<string, string>)[value] ?? value;
}

export function localizedCustomerTier(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({ VIP: "VIP", Standard: "标准" } as Record<string, string>)[value] ?? value;
}

export function localizedPolicyAction(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({ quote_discount: "折扣报价", reference_only: "仅供参考" } as Record<string, string>)[value] ?? value;
}

export function localizedPolicyField(value: string, locale: Locale) {
  if (locale === "en") return value.replaceAll("_", " ");
  return ({
    action_type: "政策类型", policy_scope: "适用范围", scope_product_id: "适用商品",
    customer_tier: "客户等级", max_discount_pct: "直接折扣上限", review_max_discount_pct: "审批折扣上限",
    exception_approval_allowed: "是否允许例外审批", title: "政策标题",
  } as Record<string, string>)[value] ?? value.replaceAll("_", " ");
}

export function localizedRetrievalMode(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({
    registry: "政策 Registry", registry_abstain: "Registry 安全弃权", conflict_graph_abstain: "政策冲突拦截",
    chroma_local: "本地向量检索", chroma_local_consensus: "本地检索一致性校验", demo: "演示检索路径",
    bm25: "关键词检索", dense: "向量检索", hybrid: "混合检索", bm25_dense: "关键词与向量混合检索",
  } as Record<string, string>)[value] ?? value;
}

export function localizedAuditAction(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({
    policy_activation_attempt: "尝试激活政策", policy_activated: "激活政策", policy_draft_uploaded: "上传政策草稿",
    policy_draft_saved: "保存政策草稿", policy_draft_confirmed: "确认政策规则", policy_draft_activated: "激活政策草稿",
    policy_rules_extraction_failed: "政策规则提取失败", policy_rules_extracted: "提取政策规则",
    policy_draft_created_from_review: "创建待审核政策草稿", policy_draft_imported: "导入政策草稿",
    policy_draft_updated: "更新政策草稿",
  } as Record<string, string>)[value] ?? value;
}

export function localizedGuardCheck(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({
    sales_capability: "销售操作权限", customer_ownership: "客户归属权限", policy_action: "政策操作类型",
    policy_scope: "政策适用范围", customer_tier: "客户等级", policy_rule_complete: "政策规则完整性",
    quantity: "商品数量", inventory: "库存", discount_limit: "折扣上限", quote_arithmetic: "报价金额计算",
    PASS: "通过", FAIL: "未通过", REVIEW: "需审批",
  } as Record<string, string>)[value] ?? value;
}

export function localizedTraceMessage(value: string | null | undefined, locale: Locale) {
  if (!value || locale === "en") return value ?? "—";
  return ({
    "Inquiry parsed using the configured OpenAI-compatible model.": "已使用配置的 OpenAI 兼容模型解析询盘。",
    "The model returned invalid fields; unsafe values were treated as missing.": "模型返回的字段无效；系统已将不安全的值按缺失信息处理。",
    "Loaded customer and product from tenant business records.": "已从当前工作区的业务数据中读取客户和商品。",
    "Customer or product could not be resolved from trusted records.": "无法从可信业务数据中识别客户或商品。",
    "Overlapping eligible policies contain conflicting structured rules; authorization is blocked.": "多个适用政策的结构化规则存在冲突，授权已阻止。",
    "No lexical evidence matched the active policy text; quote authorization is blocked.": "当前生效政策文本中没有检索到相关依据，报价授权已阻止。",
    "Retrieved evidence from the active, effective policy scope.": "已从当前生效且适用的政策范围中检索到依据。",
    "The inquiry describes multiple discount scopes; clarification is required.": "询盘涉及多个折扣范围，需要补充说明。",
    "No active policy matches this explicit scope.": "没有生效政策匹配此明确范围。",
    "Multiple eligible Registry policies remain; clarification is required.": "仍有多项 Registry 政策符合条件，需要补充说明。",
    "No active effective policy applies; execution will fail closed.": "没有适用的生效政策，系统已按安全原则停止处理。",
    "Created a quote draft after every line passed Guard.": "所有明细均通过 Guard 校验，已创建报价草稿。",
    "Created a quote draft after ALLOW.": "Guard 已允许操作，报价草稿已创建。",
    "The fixed-workflow baseline is active; dynamic tool planning is disabled.": "当前使用固定工作流基线，动态工具规划已关闭。",
    "Read-only tool planning is disabled in the deterministic demo path.": "当前为确定性规则解析路径，已关闭只读工具规划。",
    "The requested product ID is already present in trusted workspace data; deterministic loading is sufficient.": "商品编号已存在于可信工作区数据中，无需额外检索。",
    "Read-only tool planning failed; the deterministic workflow will recheck trusted data.": "只读工具规划失败，系统将通过确定性工作流重新核对可信数据。",
  } as Record<string, string>)[value] ?? value;
}

export function localizedReason(code: string | null | undefined, locale: Locale) {
  if (!code || locale === "en") return code ?? "—";
  return ({
    WITHIN_POLICY: "符合政策", DISCOUNT_LIMIT_EXCEEDED: "折扣超过政策上限", CUSTOMER_TIER_MISMATCH: "客户等级与政策不匹配",
    SALES_ROLE_REQUIRED: "需要销售角色才能创建报价", RESOURCE_NOT_OWNED: "当前账号无权为该客户报价",
    POLICY_ACTION_MISMATCH: "政策类型不适用于报价", POLICY_SCOPE_MISMATCH: "政策适用范围不匹配",
    NO_APPLICABLE_POLICY: "没有适用于该客户的有效折扣政策", POLICY_RULES_INCOMPLETE: "政策规则不完整",
    INVALID_QUANTITY: "数量必须大于零", INVENTORY_INSUFFICIENT: "库存不足", INSUFFICIENT_STOCK: "库存不足",
    MANAGER_APPROVAL_REQUIRED: "需要经理审批", CUSTOMER_NOT_FOUND: "未找到客户", PRODUCT_NOT_FOUND: "未找到商品",
    POLICY_NOT_FOUND: "没有适用的有效政策", POLICY_NOT_APPLICABLE: "政策不适用于当前询盘",
    POLICY_EVIDENCE_NOT_FOUND: "未找到相关政策依据", POLICY_CONFLICT: "有效政策规则存在冲突", NEEDS_INPUT: "询盘信息不完整",
    MISSING_REQUIRED_FIELDS: "询盘缺少必填信息", BUSINESS_DATA_NOT_FOUND: "未找到对应的客户或商品资料",
    MIXED_CURRENCY_UNSUPPORTED: "同一报价暂不支持混合币种",
    PRODUCT_ID_RESOLVED: "商品已通过可信目录识别", FROZEN_SNAPSHOT_UNAVAILABLE: "缺少可回放的冻结快照",
    ACTOR_ASSIGNMENT_CHANGED: "该身份当前不可访问此客户记录", ACTOR_NOT_FOUND: "未找到运行记录对应的身份",
    REPLAY_FAILED: "回放失败", POLICY_EVIDENCE_LOW_RELEVANCE: "未检索到与询盘相关的政策依据",
    POLICY_RETRIEVAL_AMBIGUOUS: "存在多个可能适用的政策，系统已安全弃权", POLICY_SCOPE_AMBIGUOUS: "询盘涉及多个政策范围，需要补充说明",
    POLICY_SCOPE_NOT_FOUND: "没有找到匹配该范围的生效政策", CUSTOMER_UNAVAILABLE: "无法读取当前客户资料",
    PRODUCT_QUERY_NOT_IN_INQUIRY: "商品搜索条件不在询盘内容中", TOOL_ARGUMENTS_INVALID: "工具参数无效",
    TOOL_NOT_ALLOWED: "该工具操作不被允许", TOOL_PLANNER_FAILED: "工具规划失败",
    POLICY_NOT_EFFECTIVE: "政策当前未生效",
    POLICY_DIFF_PARSE_FAILED: "无法解析询盘，未执行政策对比", POLICY_VERSION_NOT_FOUND: "未找到所选政策版本",
    POLICY_VERSION_EXISTS: "该政策版本已存在", POLICY_SCOPE_PRODUCT_NOT_FOUND: "政策指定的商品不在当前工作区",
    ADMIN_REQUIRED: "需要工作区管理员权限", POLICY_DIFF_ROLE_REQUIRED: "当前角色无权比较政策版本",
    POLICY_EXTRACTION_FAILED: "政策规则提取失败", POLICY_EXTRACTION_MODEL_NOT_CONFIGURED: "尚未配置政策解析模型",
    LLM_PROVIDER_TIMEOUT: "模型请求超时", LLM_PROVIDER_UNAVAILABLE: "模型服务暂不可用", LLM_INVALID_OUTPUT: "模型返回内容无效",
    LLM_CREDENTIALS_INVALID: "模型凭据无效", LLM_REQUEST_FAILED: "模型请求失败", LLM_REQUEST_INVALID: "模型请求配置无效",
    LOCAL_EMBEDDING_NOT_CONFIGURED: "尚未配置本地 Embedding 模型", LOCAL_EMBEDDING_UNAVAILABLE: "本地 Embedding 模型不可用",
    WORKFLOW_FAILED: "工作流执行失败", CURRENT_PASSWORD_INVALID: "当前密码不正确",
    INVALID_CREDENTIALS: "邮箱或密码不正确", EMAIL_ALREADY_REGISTERED: "该邮箱已注册", MEMBER_INACTIVE: "此成员账号已停用",
    INVITATION_INVALID: "邀请链接无效或已过期", PASSWORD_RESET_INVALID: "密码重置链接无效或已过期",
    IMAGE_TOO_LARGE: "图片不能超过大小限制", IMAGE_FORMAT_UNSUPPORTED: "图片格式不受支持", IMAGE_EMPTY: "图片文件为空",
    PRODUCT_ALREADY_EXISTS: "该商品编号已存在", CUSTOMER_IMPORT_VALIDATION_FAILED: "客户导入数据校验失败",
    PRODUCT_IMPORT_VALIDATION_FAILED: "商品导入数据校验失败", POLICY_IMPORT_VALIDATION_FAILED: "政策导入数据校验失败",
    POLICY_ID_REQUIRED: "政策编号和版本不能为空", UNSUPPORTED_POLICY_FILE: "请上传 UTF-8 编码的 Markdown 或文本文件",
    POLICY_FILE_TOO_LARGE: "政策文件不能超过 1 MiB", EMPTY_POLICY_FILE: "政策原文不能为空", INVALID_POLICY_ENCODING: "政策文件必须使用 UTF-8 编码",
    POLICY_IMPORT_CONFLICT: "导入时数据发生变化，请重新预览", POLICY_IMPORT_EMPTY: "文件中没有政策数据行",
    POLICY_IMPORT_TOO_LARGE: "导入文件超过大小限制", POLICY_DRAFT_LOCKED: "已激活的政策草稿不可修改",
    INVALID_REVIEW_LIMIT: "审批上限必须高于直接折扣上限", UNUSED_REVIEW_LIMIT: "仅启用例外审批时才能设置审批上限",
    POLICY_DRAFT_NOT_FOUND: "未找到政策草稿", POLICY_DRAFT_NOT_READY: "政策草稿尚未满足激活条件",
    INVALID_TIME_RANGE: "开始时间必须早于结束时间",
    BUSINESS_DATA_ADMIN_REQUIRED: "仅工作区管理员可管理业务数据", BUSINESS_DATA_ROLE_REQUIRED: "当前角色无权访问业务数据",
    METRICS_ROLE_REQUIRED: "当前角色无权查看运行指标", AUDIT_ROLE_REQUIRED: "当前角色无权查看审计记录",
    POLICY_ROLE_REQUIRED: "当前角色无权查看政策", EVALUATION_ROLE_REQUIRED: "当前角色无权运行检索评测",
    POLICY_IMPORT_FILE_INVALID: "政策导入文件无效",
    CUSTOMER_REQUIRED: "请选择客户", CURRENT_CONTEXT_UNAVAILABLE: "无法读取当前工作区上下文",
    EVALUATION_DATA_UNAVAILABLE: "检索评测数据暂不可用", IDEMPOTENCY_IN_PROGRESS: "相同请求正在处理中，请稍后重试",
    IDEMPOTENCY_KEY_REUSED: "请求标识已用于其他内容，请重新提交", INVALID_IDEMPOTENCY_KEY: "请求标识无效",
    IMPORT_IMAGE_FILENAME_DUPLICATE: "导入文件中存在重复图片文件名", IMPORT_IMAGE_FILENAME_INVALID: "图片文件名无效",
    IMPORT_IMAGES_TOO_LARGE: "导入图片总大小超过限制", IMPORT_TOO_MANY_IMAGES: "导入图片数量超过限制",
    INVITATION_CUSTOMER_UNAVAILABLE: "邀请中的客户资料不可用", INVITATION_NOT_FOUND: "未找到成员邀请",
    MULTI_ITEM_UPDATE_UNSUPPORTED: "暂不支持在单次更新中修改多项内容", MULTIPLE_ACTIVE_VERSIONS: "同一政策存在多个生效版本",
    OVERLAPPING_RULES: "政策规则适用范围重叠", POLICY_ACTION_REQUIRED: "请选择政策动作类型",
    POLICY_CHANGED: "政策已发生变化，请重新检查后提交", POLICY_EXTRACTION_SOURCE_TOO_LONG: "政策原文过长，无法提取规则",
    POLICY_SOURCE_CHANGED: "政策原文已发生变化，请重新确认", QUOTE_NOT_FOUND: "未找到报价记录",
    QUOTE_READ_ROLE_REQUIRED: "当前角色无权查看报价", REGISTRATION_RATE_LIMITED: "注册尝试过于频繁，请稍后重试",
    RUN_ATTEMPT_SUPERSEDED: "该运行请求已被更新的请求取代", RUN_NOT_FOUND: "未找到运行记录",
    RUNS_ROLE_REQUIRED: "当前角色无权查看运行记录", SELF_APPROVAL_FORBIDDEN: "不能审批自己提交的请求",
    UNKNOWN_DEMO_ACTOR: "当前演示身份无效", PRODUCT_IMPORT_TOO_LARGE: "商品导入文件超过大小限制",
    APPROVAL_ALREADY_COMPLETED: "该审批请求已处理", APPROVAL_IN_PROGRESS: "该审批请求正在处理中",
    APPROVAL_APPROVED: "审批已通过", APPROVAL_REJECTED: "审批已拒绝", NOT_FOUND: "未找到请求的记录",
    CUSTOMER_IMPORT_CONFLICT: "客户目录已变化，请重新预览后导入", PRODUCT_IMPORT_CONFLICT: "商品目录已变化，请重新预览后导入",
    CUSTOMER_IMPORT_EMPTY: "文件中没有客户数据行", PRODUCT_IMPORT_EMPTY: "文件中没有商品数据行",
    CUSTOMER_IMPORT_FILE_INVALID: "客户导入文件无效", PRODUCT_IMPORT_FILE_INVALID: "商品导入文件无效", IMAGE_NOT_FOUND: "未找到图片",
    MANAGER_REQUIRED: "需要经理权限", APPROVAL_NOT_FOUND: "未找到审批请求", APPROVAL_NOT_PENDING: "该请求已不在待审批状态",
    APPROVAL_CONTEXT_CHANGED: "审批期间业务或政策上下文发生变化，请重新提交",
    LOGIN_RATE_LIMITED: "登录尝试过于频繁，请稍后重试",
    CANNOT_DISABLE_SELF: "不能停用自己的账号", PASSWORD_UNCHANGED: "新密码不能与当前密码相同",
    INVITATION_ALREADY_PENDING: "该成员已有待处理邀请", INVITATION_ALREADY_USED: "邀请链接已被使用",
    MEMBER_NOT_FOUND: "未找到工作区成员", SESSION_INVALID: "登录会话无效，请重新登录",
    AUTH_REQUIRED: "请先登录",
  } as Record<string, string>)[code] ?? code;
}

export function localizedDecisionMessage(code: string | null | undefined, fallback: string | null | undefined, locale: Locale) {
  if (locale === "en" || !code) return fallback ?? "—";
  const messages: Record<string, string> = {
    WITHIN_POLICY: "请求符合当前有效政策，报价草稿已生成。",
    DISCOUNT_LIMIT_EXCEEDED: "请求折扣超过当前有效政策上限。可按政策允许范围调整后重新提交。",
    SALES_ROLE_REQUIRED: "需要销售角色账号才能创建报价。",
    RESOURCE_NOT_OWNED: "当前账号无权为所选客户创建报价。",
    NO_APPLICABLE_POLICY: "没有找到适用于该客户等级的有效折扣政策，系统未生成报价。",
    POLICY_RULES_INCOMPLETE: "当前政策缺少必要规则，系统未生成报价。",
    INVALID_QUANTITY: "商品数量必须大于零。",
    INSUFFICIENT_STOCK: "请求数量超过当前库存，系统未生成报价。",
    MANAGER_APPROVAL_REQUIRED: "该折扣需要经理审批；审批完成前不会生成报价草稿。",
    MISSING_REQUIRED_FIELDS: "询盘缺少必要信息，请补充商品、数量或折扣后重试。",
    POLICY_NOT_APPLICABLE: "当前政策不适用于这条询盘。",
    BUSINESS_DATA_NOT_FOUND: "找不到对应的客户或商品资料，请检查询盘内容。",
    MIXED_CURRENCY_UNSUPPORTED: "同一报价包含不同币种，请调整商品后重试。",
    POLICY_EVIDENCE_LOW_RELEVANCE: "没有检索到与这条询盘相关的政策依据，因此系统未创建报价。请说明折扣计划，或联系管理员检查政策。",
    POLICY_RETRIEVAL_AMBIGUOUS: "有多项有效政策可能适用于这条询盘，系统未自动选择或创建报价。请补充说明折扣范围。",
    POLICY_SCOPE_AMBIGUOUS: "询盘涉及多个政策适用范围，请明确要使用的折扣计划。",
    POLICY_SCOPE_NOT_FOUND: "没有找到适用于该询盘范围的生效政策，系统未创建报价。",
    POLICY_CONFLICT: "适用政策规则存在冲突，系统已阻止报价并要求管理员检查。",
    CUSTOMER_TIER_MISMATCH: "客户等级与政策要求不匹配，系统未生成报价。",
    PRODUCT_NOT_FOUND: "找不到询盘中指定的商品，请核对商品编号。",
    CUSTOMER_NOT_FOUND: "找不到所选客户，请检查客户资料。",
    PRODUCT_ID_RESOLVED: "已从可信商品目录中识别询盘中的商品。",
    POLICY_NOT_FOUND: "没有找到适用的生效政策，系统未创建报价。",
    POLICY_ACTION_MISMATCH: "当前政策类型不支持报价，系统未创建报价。",
    POLICY_SCOPE_MISMATCH: "当前政策适用范围与询盘不符，系统未创建报价。",
    APPROVAL_APPROVED: "经理已批准请求，系统重新校验后创建了报价草稿。",
    APPROVAL_REJECTED: "经理拒绝了该请求，系统未创建报价草稿。",
    POLICY_EVIDENCE_NOT_FOUND: "没有找到可引用的政策依据，系统未创建报价。",
    CURRENT_CONTEXT_UNAVAILABLE: "无法读取当前工作区数据，系统未创建报价。请稍后重试或联系管理员。",
    WORKFLOW_FAILED: "工作流执行失败，系统未创建报价。请稍后重试或联系管理员。",
  };
  return messages[code] ?? fallback ?? "—";
}
