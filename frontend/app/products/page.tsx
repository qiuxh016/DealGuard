"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { ImageField } from "../../components/ImageField";
import { useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Product = { id: string; name: string; currency: string; unit_price: string; stock: number; image_id: string | null };
type ProductForm = { id: string; name: string; currency: string; unit_price: string; stock: string };
type ImportPreviewRow = { row_number: number; sku: string | null; name: string | null; currency: string | null; unit_price: string | null; stock: string | number | null; image_filename: string | null; errors: string[] };
type ImportPreview = { filename: string | null; total_rows: number; valid_rows: number; invalid_rows: number; rows: ImportPreviewRow[]; unmatched_images: string[] };
const initialForm: ProductForm = { id: "", name: "", currency: "USD", unit_price: "", stock: "" };

export default function ProductsPage() {
  const { t, locale } = useLanguage();
  const [role, setRole] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(initialForm);
  const [formOpen, setFormOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importImages, setImportImages] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<Record<string, string>>({});
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const entries = importImages.map((image) => [image.name, URL.createObjectURL(image)] as const);
    setImagePreviews(Object.fromEntries(entries));
    return () => entries.forEach(([, url]) => URL.revokeObjectURL(url));
  }, [importImages]);

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
    const body = new FormData(); body.set("file", importFile); importImages.forEach((image) => body.append("images", image));
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
      setImportFile(null); setImportImages([]); setImportPreview(null); setImportOpen(false);
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
      <header className="dg-operations-head"><div><h1>{t("商品目录", "Product catalog")}</h1><p>{t("查看报价使用的商品价格和库存。价格、币种和库存由工作区数据提供。", "Manage product prices and inventory used in quotes. Product data is scoped to this workspace.")}</p></div>{role === "admin" && <div className="dg-operations-actions"><button className="dg-operations-secondary" type="button" onClick={() => { setFormOpen(false); setImportOpen((open) => !open); }}>{importOpen ? t("关闭导入", "Close import") : t("批量导入", "Bulk import")}</button><button className="dg-operations-primary" type="button" onClick={() => { setImportOpen(false); setFormOpen((open) => !open); }}>{formOpen ? t("取消", "Cancel") : t("新增商品", "Add product")}</button></div>}</header>
      {notice && <div className="dg-operations-notice" role="status">{notice} {notice.startsWith("请先登录") && <Link href="/">返回登录</Link>}</div>}
      {importOpen && role === "admin" && <section className="dg-operations-panel dg-product-import" aria-label={t("批量导入商品", "Bulk import products")}>
        <div className="dg-product-import-head"><div><h2>{t("批量导入商品", "Bulk import products")}</h2><p>{t("使用模板中的 sku、name、currency、unit_price、stock 列。最多 2,000 行、5 MiB；已有 SKU 不会被覆盖。", "Use the template columns sku, name, currency, unit_price, and stock. Up to 2,000 rows and 5 MiB. Existing SKUs are not overwritten.")}</p><p className="dg-import-image-help">{t("可同时选择商品图片，图片文件名需与 SKU 一致，例如 P-100.webp；每张最多 5 MiB，总计最多 30 MiB。", "You can add product images now. Name each image after its SKU, such as P-100.webp. Up to 5 MiB per image and 30 MiB total.")}</p></div><div className="dg-product-import-templates"><button type="button" className="dg-operations-secondary" disabled={busy} onClick={() => void downloadImportTemplate("csv")}>{t("下载 CSV 模板", "Download CSV template")}</button><button type="button" className="dg-operations-secondary" disabled={busy} onClick={() => void downloadImportTemplate("xlsx")}>{t("下载 Excel 模板", "Download Excel template")}</button></div></div>
        <form className="dg-product-import-form" onSubmit={previewProductImport}>
          <label>{t("选择商品表格", "Select product spreadsheet")}<input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { setImportFile(event.target.files?.[0] ?? null); setImportPreview(null); }} /></label>
          <label>{t("选择商品图片（可选）", "Select product images (optional)")}<input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={(event) => { setImportImages(Array.from(event.target.files ?? [])); setImportPreview(null); }} /></label>
          <button className="dg-operations-primary" type="submit" disabled={busy || !importFile}>{busy ? t("正在校验…", "Validating…") : t("预览并校验", "Preview and validate")}</button>
        </form>
        {importPreview && <div className="dg-product-import-preview">
          <div className="dg-product-import-summary"><strong>{importPreview.filename}</strong><span>{t(`共 ${importPreview.total_rows} 行`, `${importPreview.total_rows} rows`)}</span><span className="dg-import-valid">{t("可导入", "Valid")}: {importPreview.valid_rows}</span><span className={importPreview.invalid_rows ? "dg-import-invalid" : "dg-import-valid"}>{t("需修正", "Needs correction")}: {importPreview.invalid_rows}</span></div>
          {importPreview.unmatched_images.length > 0 && <p className="dg-product-import-warning">{t("这些图片未匹配到可导入的 SKU，将被忽略：", "These images do not match an importable SKU and will be ignored: ")}{importPreview.unmatched_images.join(", ")}</p>}
          {importPreview.rows.length > 0 ? <div className="dg-quote-table-wrap"><table className="dg-product-import-table"><thead><tr><th>行</th><th>SKU</th><th>名称</th><th>币种</th><th>单价</th><th>库存</th><th>{t("图片", "Image")}</th><th>{t("校验", "Validation")}</th></tr></thead><tbody>{importPreview.rows.slice(0, 50).map((row) => <tr key={row.row_number}><td>{row.row_number}</td><td>{row.sku ?? "—"}</td><td>{row.name ?? "—"}</td><td>{row.currency ?? "—"}</td><td>{row.unit_price ?? "—"}</td><td>{row.stock ?? "—"}</td><td>{row.image_filename ? <span className="dg-import-image-cell">{imagePreviews[row.image_filename] && <Image src={imagePreviews[row.image_filename]} width={38} height={38} unoptimized alt="" />}{row.image_filename}</span> : "—"}</td><td>{row.errors.length ? <span className="dg-import-invalid">{row.errors.join(" ")}</span> : <span className="dg-import-valid">{t("通过", "Valid")}</span>}</td></tr>)}</tbody></table>{importPreview.rows.length > 50 && <p className="dg-product-import-more">{t("仅显示前 50 行，确认时会提交并重新校验完整文件。", "Showing the first 50 rows. The full file will be validated again on import.")}</p>}</div> : <p className="dg-product-import-more">{t("文件中没有商品数据行。", "The file contains no product rows.")}</p>}
          {importPreview.invalid_rows > 0 && <p className="dg-product-import-warning">存在错误时整批不会导入。请修正表格后重新选择并预览。</p>}
          <div className="dg-form-actions"><button type="button" className="dg-operations-primary" disabled={busy || !importPreview.valid_rows || !!importPreview.invalid_rows || !importFile} onClick={() => void confirmProductImport()}>{t("确认导入", "Import")} {importPreview.valid_rows} {t("件商品", "products")}</button></div>
        </div>}
      </section>}
      {formOpen && role === "admin" && <section className="dg-operations-panel" aria-label="新增商品表单"><form className="dg-operations-form" onSubmit={addProduct}>
        <p className="dg-image-help">{t("保存商品后，可在列表中上传或更换商品图片。支持 PNG、JPEG、WebP，最大 5 MiB。", "After saving, upload or replace a product image from the list. PNG, JPEG, and WebP up to 5 MiB.")}</p>
        <label>{t("商品编号", "SKU")}<input value={form.id} onChange={(event) => setForm((current) => ({ ...current, id: event.target.value.toUpperCase() }))} pattern="[A-Za-z0-9_-]+" minLength={2} maxLength={80} placeholder={t("例如 P-200", "e.g. P-200")} required /></label>
        <label>{t("商品名称", "Product name")}<input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} minLength={2} maxLength={160} placeholder={t("输入商品名称", "Enter a product name")} required /></label>
        <label>{t("币种", "Currency")}<select value={form.currency} onChange={(event) => setForm((current) => ({ ...current, currency: event.target.value }))}><option value="USD">USD</option><option value="CNY">CNY</option><option value="EUR">EUR</option></select></label>
        <label>{t("单价", "Unit price")}<input type="number" min="0.01" step="0.01" value={form.unit_price} onChange={(event) => setForm((current) => ({ ...current, unit_price: event.target.value }))} placeholder="0.00" required /></label>
        <label>{t("可用库存", "Available stock")}<input type="number" min="0" step="1" value={form.stock} onChange={(event) => setForm((current) => ({ ...current, stock: event.target.value }))} placeholder="0" required /></label>
        <div className="dg-form-actions"><button className="dg-operations-secondary" type="button" onClick={() => setFormOpen(false)}>{t("取消", "Cancel")}</button><button className="dg-operations-primary" disabled={busy}>{busy ? t("正在保存…", "Saving…") : t("保存商品", "Save product")}</button></div>
      </form></section>}
      <div className="dg-operations-toolbar"><input className="dg-operations-search" aria-label={t("搜索商品", "Search products")} placeholder={t("搜索商品名称或商品编号", "Search by product name or SKU")} value={search} onChange={(event) => setSearch(event.target.value)} /></div>
      <section className="dg-stock-overview" aria-label={t("库存概览", "Stock overview")}><div className="dg-stock-overview-head"><div><h2>{t("库存概览", "Stock overview")}</h2><p>{t("按当前商品目录展示可用库存", "Available inventory from the current catalog")}</p></div><strong>{products.reduce((total, item) => total + item.stock, 0).toLocaleString()} <small>{t("件", "units")}</small></strong></div><div className="dg-stock-bars">{visibleProducts.slice(0, 6).map((product) => { const maxStock = Math.max(...visibleProducts.map((item) => item.stock), 1); return <div className="dg-stock-row" key={product.id}><div><span>{product.id} · {product.name}</span><strong>{product.stock.toLocaleString()}</strong></div><div className="dg-stock-track"><i style={{ width: `${product.stock > 0 ? Math.max(3, product.stock / maxStock * 100) : 0}%` }} /></div></div>; })}{visibleProducts.length > 6 && <span className="dg-stock-more">+{visibleProducts.length - 6} {t("种商品未显示", "more products")}</span>}</div></section>
      <section className="dg-operations-panel dg-catalog-panel"><div className="dg-quote-list-head"><strong>{t("商品列表", "Products")}</strong><span>{visibleProducts.length} {t("项", "items")}</span></div><div className="dg-quote-table-wrap"><table className="dg-catalog-table"><thead><tr><th>{t("商品编号", "SKU")}</th><th>{t("商品名称", "Product")}</th><th>{t("图片", "Image")}</th><th>{t("单价", "Unit price")}</th><th>{t("可用库存", "Stock")}</th><th>{t("报价", "Quote")}</th></tr></thead><tbody>
        {visibleProducts.map((product) => <tr key={product.id}><td className="dg-catalog-code">{product.id}</td><td><strong>{product.name}</strong></td><td><ImageField imageId={product.image_id ?? null} alt={`${product.name} ${t("图片", "image")}`} label={t("上传商品图", "Upload product image")} uploadPath={`/api/v1/products/${encodeURIComponent(product.id)}/image`} removePath={`/api/v1/products/${encodeURIComponent(product.id)}/image`} editable={role === "admin"} onChange={(imageId) => setProducts((current) => current.map((item) => item.id === product.id ? { ...item, image_id: imageId } : item))} /></td><td className="dg-catalog-value">{new Intl.NumberFormat(locale === "zh" ? "zh-CN" : "en-US", { style: "currency", currency: product.currency }).format(Number(product.unit_price))}</td><td className="dg-catalog-value">{product.stock.toLocaleString()}</td><td><button type="button" className="dg-operations-secondary" onClick={() => { window.location.href = `/?product=${encodeURIComponent(product.id)}`; }}>{t("开始报价", "Create quote")}</button></td></tr>)}
      </tbody></table>{!visibleProducts.length && <div className="dg-operations-empty">没有匹配商品。{role === "admin" ? "添加商品后即可用于报价。" : "请联系管理员维护商品目录。"}</div>}</div></section>
    </main>
  </div>;
}
