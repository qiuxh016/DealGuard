"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { BrandMark } from "./BrandMark";

type Identity = { display_name?: string; actor_id?: string; role?: string };
type Props = { role?: string; displayName?: string; onLogout?: () => void | Promise<void> };

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const destinations = [
  ["/", "工作台"],
  ["/runs", "运行记录"],
  ["/quotes", "报价中心"],
  ["/customers", "客户"],
  ["/products", "商品"],
  ["/account", "账户"],
] as const;

export function WorkspaceNav({ role: roleProp, displayName: nameProp, onLogout }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity>({});

  useEffect(() => {
    if (roleProp && nameProp) return;
    let active = true;
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => response.ok ? await response.json() as Identity : {})
      .then((value) => { if (active) setIdentity(value); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [nameProp, roleProp]);

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
      <Link className="dg-workspace-brand" href="/" aria-label="DealGuard 工作台">
        <span className="dg-workspace-mark"><BrandMark /></span>
        <span className="dg-workspace-brand-copy"><strong>DealGuard</strong><small>Policy-aware transaction workspace</small></span>
      </Link>
      <nav className="dg-workspace-links" aria-label="工作区导航">
        {destinations.map(([href, label]) => (
          <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>{label}</Link>
        ))}
        {(role === "manager" || role === "admin") && <Link href="/evaluation" aria-current={pathname === "/evaluation" ? "page" : undefined}>检索评测</Link>}
        {role === "admin" && <Link href="/audit" aria-current={pathname === "/audit" ? "page" : undefined}>政策审计</Link>}
      </nav>
      <div className="dg-workspace-session">
        {displayName && <span title={displayName}>{displayName}</span>}
        <button type="button" onClick={() => void logout()}>退出登录</button>
      </div>
    </header>
  );
}
