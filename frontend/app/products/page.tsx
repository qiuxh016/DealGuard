"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Product = { id: string; name: string; currency: string; unit_price: string; stock: number };
type ProductForm = { id: string; name: string; currency: string; unit_price: string; stock: string };
const initialForm: ProductForm = { id: "", name: "", currency: "USD", unit_price: "", stock: "" };

export default function ProductsPage() {
  const [role, setRole] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(initialForm);
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function loadProducts() {
    try {
      const response = await fetch(`${API}/api/v1/products`, { credentials: "include", cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法读取商品");
      setProducts(payload);
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法读取商品"); }
  }

  useEffect(() => {
    void fetch(`${API}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("请先登录后查看商品"); const user = await response.json(); setRole(user.role); await loadProducts(); })
      .catch((error) => setNotice(error instanceof Error ? error.message : "请先登录后查看商品"));
  }, []);

  const visibleProducts = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return products.filter((product) => !normalized || product.name.toLowerCase().includes(normalized) || product.id.toLowerCase().includes(normalized));
  }, [products, search]);

  async function addProduct(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/products`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...form, id: form.id.trim().toUpperCase(), unit_price: form.unit_price, stock: Number(form.stock) }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "添加商品失败");
      setProducts((current) => [...current, payload].sort((a, b) => a.id.localeCompare(b.id)));
      setForm(initialForm); setFormOpen(false); setNotice("商品已添加到当前工作区");
    } catch (error) { setNotice(error instanceof Error ? error.message : "添加商品失败"); }
    finally { setBusy(false); }
  }

  return <div className="dg-operations">
    <WorkspaceNav role={role} />
    <main className="dg-operations-main">
      <header className="dg-operations-head"><div><h1>商品目录</h1><p>查看报价使用的商品价格和库存。价格、币种和库存由工作区数据提供。</p></div>{role === "admin" && <button className="dg-operations-primary" type="button" onClick={() => setFormOpen((open) => !open)}>{formOpen ? "取消" : "新增商品"}</button>}</header>
      {notice && <div className="dg-operations-notice" role="status">{notice} {notice.startsWith("请先登录") && <Link href="/">返回登录</Link>}</div>}
      {formOpen && role === "admin" && <section className="dg-operations-panel" aria-label="新增商品表单"><form className="dg-operations-form" onSubmit={addProduct}>
        <label>商品编号<input value={form.id} onChange={(event) => setForm((current) => ({ ...current, id: event.target.value.toUpperCase() }))} pattern="[A-Za-z0-9_-]+" minLength={2} maxLength={80} placeholder="例如 P-200" required /></label>
        <label>商品名称<input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} minLength={2} maxLength={160} placeholder="输入商品名称" required /></label>
        <label>币种<select value={form.currency} onChange={(event) => setForm((current) => ({ ...current, currency: event.target.value }))}><option value="USD">USD</option><option value="CNY">CNY</option><option value="EUR">EUR</option></select></label>
        <label>单价<input type="number" min="0.01" step="0.01" value={form.unit_price} onChange={(event) => setForm((current) => ({ ...current, unit_price: event.target.value }))} placeholder="0.00" required /></label>
        <label>可用库存<input type="number" min="0" step="1" value={form.stock} onChange={(event) => setForm((current) => ({ ...current, stock: event.target.value }))} placeholder="0" required /></label>
        <div className="dg-form-actions"><button className="dg-operations-secondary" type="button" onClick={() => setFormOpen(false)}>取消</button><button className="dg-operations-primary" disabled={busy}>{busy ? "正在保存…" : "保存商品"}</button></div>
      </form></section>}
      <div className="dg-operations-toolbar"><input className="dg-operations-search" aria-label="搜索商品" placeholder="搜索商品名称或商品编号" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
      <section className="dg-operations-panel dg-catalog-panel"><div className="dg-quote-list-head"><strong>商品列表</strong><span>{visibleProducts.length} 项</span></div><div className="dg-quote-table-wrap"><table className="dg-catalog-table"><thead><tr><th>商品编号</th><th>商品名称</th><th>单价</th><th>可用库存</th><th>报价</th></tr></thead><tbody>
        {visibleProducts.map((product) => <tr key={product.id}><td className="dg-catalog-code">{product.id}</td><td><strong>{product.name}</strong></td><td className="dg-catalog-value">{new Intl.NumberFormat("en-US", { style: "currency", currency: product.currency }).format(Number(product.unit_price))}</td><td className="dg-catalog-value">{product.stock.toLocaleString()}</td><td><button type="button" className="dg-operations-secondary" onClick={() => { window.location.href = `/?product=${encodeURIComponent(product.id)}`; }}>开始报价</button></td></tr>)}
      </tbody></table>{!visibleProducts.length && <div className="dg-operations-empty">没有匹配商品。{role === "admin" ? "添加商品后即可用于报价。" : "请联系管理员维护商品目录。"}</div>}</div></section>
    </main>
  </div>;
}
