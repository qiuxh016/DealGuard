"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useLanguage } from "./LanguageProvider";

const features = [
  { href: "/", zh: "询盘工作台", en: "Inquiry workspace", copyZh: "提交中英文询盘，查看解析、政策依据和 Guard 决策；获准后生成报价草稿。", copyEn: "Submit an inquiry in Chinese or English, review policy evidence and Guard decisions, and create a draft quote when allowed." },
  { href: "/runs", zh: "运行记录与 Trace", en: "Run history and traces", copyZh: "查看历史决策、执行步骤、政策引用及每项 Guard 检查。", copyEn: "Review past decisions, execution steps, policy citations, and each Guard check." },
  { href: "/quotes", zh: "报价中心", en: "Quote center", copyZh: "逐项核对商品、单价、数量、折扣和总额，并导出 CSV。", copyEn: "Review products, unit prices, quantities, discounts, and totals; export CSV." },
  { href: "/customers", zh: "客户目录", en: "Customers", copyZh: "管理客户等级、Logo 和客户表格导入。", copyEn: "Manage customer tiers, logos, and spreadsheet imports." },
  { href: "/products", zh: "商品目录", en: "Products", copyZh: "管理商品价格、库存、商品图和批量导入。", copyEn: "Manage product pricing, inventory, images, and bulk imports." },
  { href: "/policy-impact", zh: "政策影响分析", en: "Policy impact", copyZh: "回放历史询盘，比较待激活政策对决策的影响。", copyEn: "Replay historical inquiries and compare the impact of a policy before activation." },
  { href: "/account", zh: "账户与团队", en: "Account and team", copyZh: "更新个人资料和密码，管理员可邀请及管理团队成员。", copyEn: "Update your profile and password; administrators can invite and manage team members." },
];

export function GettingStartedWidget() {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  async function copyCredentials() {
    try {
      await navigator.clipboard.writeText("管理员账号\n邮箱：demo@dealguard.local\n密码：DealGuard-Showcase-2026!\n\n经理账号\n邮箱：manager@dealguard.local\n密码：DealGuard-Manager-2026!\n\n销售账号\n邮箱：sales@dealguard.local\n密码：DealGuard-Sales-2026!\n负责客户：BluePeak Distribution（DG-C-005）");
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch { setCopied(false); }
  }

  return <div className="dg-handbook-widget">
    {open && <section className="dg-handbook-panel" id="dg-handbook-panel" aria-labelledby="dg-handbook-title">
      <header className="dg-handbook-head"><div><span>{t("DEALGUARD 指南", "DEALGUARD GUIDE")}</span><h2 id="dg-handbook-title">{t("新手手册", "Getting started")}</h2></div><button type="button" onClick={() => setOpen(false)} aria-label={t("关闭手册", "Close guide")}>×</button></header>
      <div className="dg-handbook-content">
        <p className="dg-handbook-intro">{t("快速了解工作区功能。所有报价均为草稿，实际授权由 Guard 和当前有效政策决定。", "Explore the workspace. Quotes remain drafts; Guard and active policies determine authorization.")}</p>
        <nav className="dg-handbook-features" aria-label={t("功能介绍", "Feature guide")}>
          {features.map((feature) => <Link href={feature.href} key={feature.href} onClick={() => setOpen(false)}><span><strong>{t(feature.zh, feature.en)}</strong><small>{t(feature.copyZh, feature.copyEn)}</small></span><b aria-hidden="true">↗</b></Link>)}
        </nav>
        <section className="dg-handbook-demo"><div className="dg-handbook-accounts">
          <div className="dg-handbook-account"><span>{t("管理员账号", "Administrator account")}</span><strong>demo@dealguard.local</strong><code>DealGuard-Showcase-2026!</code></div>
          <div className="dg-handbook-account"><span>{t("经理账号 · 审批与团队数据查看", "Manager account · approvals and team data")}</span><strong>manager@dealguard.local</strong><code>DealGuard-Manager-2026!</code></div>
          <div className="dg-handbook-account"><span>{t("销售账号 · 负责 BluePeak Distribution", "Sales account · BluePeak Distribution")}</span><strong>sales@dealguard.local</strong><code>DealGuard-Sales-2026!</code><small>DG-C-005</small></div>
        </div><button type="button" onClick={() => void copyCredentials()}>{copied ? t("已复制", "Copied") : t("复制全部账号", "Copy all credentials")}</button></section>
        <p className="dg-handbook-demo-note">{t("该账号用于功能演示。请勿在此账号中存放真实业务或敏感数据。", "This account is for showcasing the product. Do not store real business or sensitive data in it.")}</p>
      </div>
    </section>}
    <button type="button" className={`dg-handbook-launcher ${open ? "is-open" : ""}`} aria-expanded={open} aria-controls="dg-handbook-panel" onClick={() => setOpen((value) => !value)} aria-label={open ? t("关闭新手手册", "Close getting started guide") : t("打开新手手册", "Open getting started guide")}>
      {open ? <span aria-hidden="true">×</span> : <svg viewBox="0 0 24 24" aria-hidden="true" fill="none"><path d="M4.5 5.75A2.25 2.25 0 0 1 6.75 3.5H19v15H6.75A2.25 2.25 0 0 0 4.5 20.75v-15Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"/><path d="M4.5 18.5c.5-.65 1.25-1 2.25-1H19M8 7.5h7M8 10.5h7" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>}
    </button>
  </div>;
}
