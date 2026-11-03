"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type Props = { role?: string };

const destinations = [
  ["/", "工作台"],
  ["/runs", "运行记录"],
  ["/quotes", "报价中心"],
  ["/customers", "客户"],
  ["/products", "商品"],
] as const;

export function WorkspaceNav({ role = "" }: Props) {
  const pathname = usePathname();
  return (
    <nav className="dg-workspace-nav" aria-label="工作区导航">
      <Link className="dg-workspace-brand" href="/">DealGuard</Link>
      <div className="dg-workspace-links">
        {destinations.map(([href, label]) => (
          <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>
            {label}
          </Link>
        ))}
        {role === "manager" && <Link href="/#approvals">审批</Link>}
        {role === "admin" && <Link href="/#policy-center">政策</Link>}
        {(role === "manager" || role === "admin") && <Link href="/evaluation">检索评测</Link>}
        {role === "admin" && <Link href="/audit">政策审计</Link>}
      </div>
    </nav>
  );
}
