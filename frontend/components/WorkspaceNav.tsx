"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Image from "next/image";
import { BrandMark } from "./BrandMark";
import { LanguageToggle, useLanguage } from "./LanguageProvider";

type Identity = { display_name?: string; actor_id?: string; role?: string; image_id?: string | null };
type Props = { role?: string; displayName?: string; onLogout?: () => void | Promise<void> };

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const destinations = [
  ["/", "工作台", "Workspace"],
  ["/runs", "运行记录", "Run history"],
  ["/quotes", "报价中心", "Quotes"],
  ["/customers", "客户", "Customers"],
  ["/products", "商品", "Products"],
  ["/account", "账户", "Account"],
] as const;

export function WorkspaceNav({ role: roleProp, displayName: nameProp, onLogout }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const { t } = useLanguage();
  const [identity, setIdentity] = useState<Identity>({});

  useEffect(() => {
    let active = true;
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => response.ok ? await response.json() as Identity : {})
      .then((value) => { if (active) setIdentity(value); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [nameProp, roleProp]);

  useEffect(() => {
    const updateAvatar = (event: Event) => {
      const imageId = (event as CustomEvent<string | null>).detail;
      setIdentity((current) => ({ ...current, image_id: imageId }));
    };
    window.addEventListener("dealguard-profile-avatar", updateAvatar);
    return () => window.removeEventListener("dealguard-profile-avatar", updateAvatar);
  }, []);

  const role = roleProp || identity.role || "";
  const displayName = nameProp || identity.display_name || identity.actor_id || "";

  async function logout() {
    if (onLogout) {
      await onLogout();
      return;
    }
    await fetch(`${API}/api/v1/auth/logout`, { method: "POST", credentials: "include" });
    router.replace("/");
    router.refresh();
  }

  return (
    <header className="dg-workspace-nav">
      <Link className="dg-workspace-brand" href="/" aria-label={t("DealGuard 工作台", "DealGuard workspace")}>
        <span className="dg-workspace-mark"><BrandMark /></span>
        <span className="dg-workspace-brand-copy"><strong>DealGuard</strong><small>{t("政策驱动的交易工作台", "Policy-aware transaction workspace")}</small></span>
      </Link>
      <nav className="dg-workspace-links" aria-label={t("工作区导航", "Workspace navigation")}>
        {destinations.map(([href, labelZh, labelEn]) => (
          <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>{t(labelZh, labelEn)}</Link>
        ))}
        {(role === "manager" || role === "admin") && <Link href="/evaluation" aria-current={pathname === "/evaluation" ? "page" : undefined}>{t("检索评测", "Retrieval evaluation")}</Link>}
        {role === "admin" && <Link href="/audit" aria-current={pathname === "/audit" ? "page" : undefined}>{t("政策审计", "Policy audit")}</Link>}
        {role === "admin" && <Link href="/policy-impact" aria-current={pathname === "/policy-impact" ? "page" : undefined}>{t("政策影响分析", "Policy impact")}</Link>}
      </nav>
      <div className="dg-workspace-session">
        {displayName && <span className="dg-workspace-user">{identity.image_id ? <Image className="dg-workspace-avatar" src={`${API}/api/v1/media/${encodeURIComponent(identity.image_id)}`} alt={t("账户头像", "Account avatar")} title={displayName} width={34} height={34} unoptimized /> : <span className="dg-workspace-avatar dg-workspace-avatar-fallback" aria-label={t("账户头像", "Account avatar")} title={displayName}>{displayName.slice(0, 1).toUpperCase()}</span>}</span>}
        <LanguageToggle />
        <button type="button" onClick={() => void logout()}>{t("退出登录", "Sign out")}</button>
      </div>
    </header>
  );
}
