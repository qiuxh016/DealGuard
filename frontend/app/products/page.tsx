"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Product = { id: string; name: string; currency: string; unit_price: string; stock: number };
type ProductForm = { id: string; name: string; currency: string; unit_price: string; stock: string };
type ImportPreviewRow = { row_number: number; sku: string | null; name: string | null; currency: string | null; unit_price: string | null; stock: string | number | null; errors: string[] };
type ImportPreview = { filename: string | null; total_rows: number; valid_rows: number; invalid_rows: number; rows: ImportPreviewRow[] };
const initialForm: ProductForm = { id: "", name: "", currency: "USD", unit_price: "", stock: "" };

export default function ProductsPage() {
  const [role, setRole] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(initialForm);
  const [formOpen, setFormOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
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

  async function requestProductImport(path: string) {
    if (!importFile) throw new Error("请先选择 CSV 或 XLSX 文件");
    const body = new FormData(); body.set("file", importFile);
    const response = await fetch(`${API}/api/v1/products/import${path}`, { method: "POST", credentials: "include", body });
    const payload = await response.json();
    if (!response.ok) {
      if (payload?.detail?.preview) setImportPreview(payload.detail.preview as ImportPreview);
      throw new Error(payload?.detail?.message ?? "商品导入失败");
    }
    return payload;
  }

  async function previewProductImport(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(""); setImportPreview(null);
    try {
      const preview = await requestProductImport("/preview");
      setImportPreview(preview as ImportPreview);
      setNotice(`预览完成：${preview.valid_rows} 行可导入，${preview.invalid_rows} 行需要修正。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "商品预览失败"); }
    finally { setBusy(false); }
  }

  async function confirmProductImport() {
    setBusy(true); setNotice("");
    try {
      const result = await requestProductImport("");
      setProducts((current) => [...current, ...result.products].sort((a, b) => a.id.localeCompare(b.id)));
      setImportFile(null); setImportPreview(null); setImportOpen(false);
      setNotice(`已导入 ${result.imported_count} 件商品。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "商品导入失败"); }
    finally { setBusy(false); }
  }

  async function downloadImportTemplate(format: "csv" | "xlsx") {
    setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/products/import/template?format=${format}`, { credentials: "include", cache: "no-store" });
      if (!response.ok) throw new Error("模板下载失败；请确认当前账号是工作区管理员");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `dealguard-products-template.${format}`; link.click(); URL.revokeObjectURL(url);
    } catch (error) { setNotice(error instanceof Error ? error.message : "模板下载失败"); }
    finally { setBusy(false); }
  }

  return <div className="dg-operations">
    <WorkspaceNav role={role} />
    <main className="dg-operations-main">
      <header className="dg-operations-head"><div><h1>商品目录</h1><p>查看报价使用的商品价格和库存。价格、币种和库存由工作区数据提供。</p></div>{role === "admin" && <div className="dg-operations-actions"><button className="dg-operations-secondary" type="button" onClick={() => { setFormOpen(false); setImportOpen((open) => !open); }}>{importOpen ? "关闭导入" : "批量导入"}</button><button className="dg-operations-primary" type="button" onClick={() => { setImportOpen(false); setFormOpen((open) => !open); }}>{formOpen ? "取消" : "新增商品"}</button></div>}</header>
      {notice && <div className="dg-operations-notice" role="status">{notice} {notice.startsWith("请先登录") && <Link href="/">返回登录</Link>}</div>}
      {importOpen && role === "admin" && <section className="dg-operations-panel dg-product-import" aria-label="批量导入商品">
        <div className="dg-product-import-head"><div><h2>批量导入商品</h2><p>使用模板中的 sku、name、currency、unit_price、stock 列。最多 2,000 行、5 MiB；已有 SKU 不会被覆盖。</p></div><div className="dg-product-import-templates"><button type="button" className="dg-operations-secondary" disabled={busy} onClick={() => void downloadImportTemplate("csv")}>下载 CSV 模板</button><button type="button" className="dg-operations-secondary" disabled={busy} onClick={() => void downloadImportTemplate("xlsx")}>下载 Excel 模板</button></div></div>
        <form className="dg-product-import-form" onSubmit={previewProductImport}>
          <label>选择商品表格<input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { setImportFile(event.target.files?.[0] ?? null); setImportPreview(null); }} /></label>
          <button className="dg-operations-primary" type="submit" disabled={busy || !importFile}>{busy ? "正在校验…" : "预览并校验"}</button>
        </form>
        {importPreview && <div className="dg-product-import-preview">
          <div className="dg-product-import-summary"><strong>{importPreview.filename}</strong><span>共 {importPreview.total_rows} 行</span><span className="dg-import-valid">可导入 {importPreview.valid_rows}</span><span className={importPreview.invalid_rows ? "dg-import-invalid" : "dg-import-valid"}>需修正 {importPreview.invalid_rows}</span></div>
          {importPreview.rows.length > 0 ? <div className="dg-quote-table-wrap"><table className="dg-product-import-table"><thead><tr><th>行</th><th>SKU</th><th>名称</th><th>币种</th><th>单价</th><th>库存</th><th>校验</th></tr></thead><tbody>{importPreview.rows.slice(0, 50).map((row) => <tr key={row.row_number}><td>{row.row_number}</td><td>{row.sku ?? "—"}</td><td>{row.name ?? "—"}</td><td>{row.currency ?? "—"}</td><td>{row.unit_price ?? "—"}</td><td>{row.stock ?? "—"}</td><td>{row.errors.length ? <span className="dg-import-invalid">{row.errors.join(" ")}</span> : <span className="dg-import-valid">通过</span>}</td></tr>)}</tbody></table>{importPreview.rows.length > 50 && <p className="dg-product-import-more">仅显示前 50 行，确认时会提交并重新校验完整文件。</p>}</div> : <p className="dg-product-import-more">文件中没有商品数据行。</p>}
          {importPreview.invalid_rows > 0 && <p className="dg-product-import-warning">存在错误时整批不会导入。请修正表格后重新选择并预览。</p>}
          <div className="dg-form-actions"><button type="button" className="dg-operations-primary" disabled={busy || !importPreview.valid_rows || !!importPreview.invalid_rows || !importFile} onClick={() => void confirmProductImport()}>确认导入 {importPreview.valid_rows} 件商品</button></div>
        </div>}
      </section>}
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
