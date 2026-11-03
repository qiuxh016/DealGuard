"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Customer = { id: string; name: string; tier: "VIP" | "Standard" };

export default function CustomersPage() {
  const [role, setRole] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [tier, setTier] = useState<Customer["tier"]>("Standard");
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function loadCustomers() {
    try {
      const response = await fetch(`${API}/api/v1/customers`, { credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取客户");
      setCustomers(payload);
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法读取客户"); }
  }

  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("请先登录后查看客户"); const user = await response.json(); setRole(user.role); await loadCustomers(); })
      .catch((error) => setNotice(error instanceof Error ? error.message : "请先登录后查看客户"));
  }, []);

  const visibleCustomers = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return customers.filter((customer) => !normalized || customer.name.toLowerCase().includes(normalized) || customer.id.toLowerCase().includes(normalized));
  }, [customers, search]);

  async function addCustomer(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/customers`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, tier }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "添加客户失败");
      setCustomers((current) => [...current, payload].sort((a, b) => a.name.localeCompare(b.name)));
      setName(""); setTier("Standard"); setFormOpen(false); setNotice("客户已添加到当前工作区");
    } catch (error) { setNotice(error instanceof Error ? error.message : "添加客户失败"); }
    finally { setBusy(false); }
  }

  return <div className="dg-operations">
    <WorkspaceNav role={role} />
    <main className="dg-operations-main">
      <header className="dg-operations-head"><div><h1>客户</h1><p>工作区客户资料与服务等级。销售账号仅能查看分配给自己的客户。</p></div>{role === "admin" && <button className="dg-operations-primary" type="button" onClick={() => setFormOpen((open) => !open)}>{formOpen ? "取消" : "新增客户"}</button>}</header>
      {notice && <div className="dg-operations-notice" role="status">{notice} {notice.startsWith("请先登录") && <Link href="/">返回登录</Link>}</div>}
      {formOpen && role === "admin" && <section className="dg-operations-panel" aria-label="新增客户表单"><form className="dg-operations-form" onSubmit={addCustomer}>
        <label className="dg-form-wide">客户名称<input value={name} onChange={(event) => setName(event.target.value)} minLength={2} maxLength={160} placeholder="输入公司或客户名称" required /></label>
        <label>客户等级<select value={tier} onChange={(event) => setTier(event.target.value as Customer["tier"])}><option value="Standard">Standard</option><option value="VIP">VIP</option></select></label>
        <div className="dg-form-actions"><button className="dg-operations-secondary" type="button" onClick={() => setFormOpen(false)}>取消</button><button className="dg-operations-primary" disabled={busy}>{busy ? "正在保存…" : "保存客户"}</button></div>
      </form></section>}
      <div className="dg-operations-toolbar"><input className="dg-operations-search" aria-label="搜索客户" placeholder="搜索客户名称或客户编号" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
      <section className="dg-operations-panel dg-catalog-panel"><div className="dg-quote-list-head"><strong>客户列表</strong><span>{visibleCustomers.length} 条</span></div><div className="dg-quote-table-wrap"><table className="dg-catalog-table"><thead><tr><th>客户编号</th><th>客户名称</th><th>客户等级</th><th>可用操作</th></tr></thead><tbody>
        {visibleCustomers.map((customer) => <tr key={customer.id}><td className="dg-catalog-code">{customer.id}</td><td><strong>{customer.name}</strong></td><td>{customer.tier}</td><td><button type="button" className="dg-operations-secondary" onClick={() => { window.location.href = `/?customer=${encodeURIComponent(customer.id)}`; }}>创建询盘</button></td></tr>)}
      </tbody></table>{!visibleCustomers.length && <div className="dg-operations-empty">没有匹配客户。{role === "admin" ? "可添加客户后再创建询盘。" : "请联系管理员维护客户资料。"}</div>}</div></section>
    </main>
  </div>;
}
