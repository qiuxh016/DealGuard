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
    DRAFT: "报价草稿", active: "生效中", inactive: "未生效", draft: "草稿",
    replayed: "已回放", skipped: "已跳过", pending: "待处理", approved: "已批准", rejected: "已拒绝",
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
    REPLAY_FAILED: "回放失败",
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
  };
  return messages[code] ?? fallback ?? "—";
}
