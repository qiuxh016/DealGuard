"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { ImageField } from "../../components/ImageField";
import { localizedCustomerTier, useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";
import "../operations.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Customer = { id: string; name: string; tier: "VIP" | "Standard"; image_id: string | null };
type CustomerImportRow = { row_number: number; customer_id: string | null; name: string | null; tier: string | null; image_filename: string | null; errors: string[] };
type CustomerImportPreview = { filename: string | null; total_rows: number; valid_rows: number; invalid_rows: number; rows: CustomerImportRow[]; unmatched_images: string[] };

export default function CustomersPage() {
  const { locale, t } = useLanguage();
  const [role, setRole] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [tier, setTier] = useState<Customer["tier"]>("Standard");
  const [newCustomerImage, setNewCustomerImage] = useState<File | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importImages, setImportImages] = useState<File[]>([]);
  const [imagePreviews, setImagePreviews] = useState<Record<string, string>>({});
  const [importPreview, setImportPreview] = useState<CustomerImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const entries = importImages.map((image) => [image.name, URL.createObjectURL(image)] as const);
    setImagePreviews(Object.fromEntries(entries));
    return () => entries.forEach(([, url]) => URL.revokeObjectURL(url));
  }, [importImages]);

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
      if (newCustomerImage && newCustomerImage.size > 5 * 1024 * 1024) throw new Error(t("图片不能超过 5 MiB", "Image must be 5 MiB or smaller"));
      const response = await fetch(`${API}/api/v1/customers`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, tier }) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "添加客户失败");
      let savedCustomer = payload as Customer;
      let imageError = "";
      if (newCustomerImage) {
        try {
          const imageBody = new FormData(); imageBody.set("file", newCustomerImage);
          const imageResponse = await fetch(`${API}/api/v1/customers/${encodeURIComponent(payload.id)}/image`, { method: "POST", credentials: "include", body: imageBody });
          const imagePayload = await imageResponse.json();
          if (!imageResponse.ok) throw new Error(imagePayload?.detail?.message ?? t("图片上传失败", "Image upload failed"));
          savedCustomer = { ...savedCustomer, image_id: imagePayload.image_id };
        } catch (error) { imageError = error instanceof Error ? error.message : t("图片上传失败", "Image upload failed"); }
      }
      setCustomers((current) => [...current, savedCustomer].sort((a, b) => a.name.localeCompare(b.name)));
      setName(""); setTier("Standard"); setNewCustomerImage(null); setFormOpen(false);
      setNotice(imageError ? t(`客户已创建，但图片上传失败：${imageError}`, `Customer created, but the image upload failed: ${imageError}`) : t("客户已添加到当前工作区", "Customer added to this workspace"));
    } catch (error) { setNotice(error instanceof Error ? error.message : "添加客户失败"); }
    finally { setBusy(false); }
  }

  async function requestCustomerImport(path: string) {
    if (!importFile) throw new Error(t("请先选择 CSV 或 XLSX 文件", "Select a CSV or XLSX file first"));
    const body = new FormData(); body.set("file", importFile); importImages.forEach((image) => body.append("images", image));
    const response = await fetch(`${API}/api/v1/customers/import${path}`, { method: "POST", credentials: "include", body });
    const payload = await response.json();
    if (!response.ok) {
      if (payload?.detail?.preview) setImportPreview(payload.detail.preview as CustomerImportPreview);
      throw new Error(payload?.detail?.message ?? t("客户导入失败", "Customer import failed"));
    }
    return payload;
  }

  async function previewCustomerImport(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(""); setImportPreview(null);
    try {
      const preview = await requestCustomerImport("/preview");
      setImportPreview(preview as CustomerImportPreview);
      setNotice(t(`预览完成：${preview.valid_rows} 行可导入，${preview.invalid_rows} 行需要修正。`, `Preview complete: ${preview.valid_rows} valid rows, ${preview.invalid_rows} need correction.`));
    } catch (error) { setNotice(error instanceof Error ? error.message : t("客户预览失败", "Customer preview failed")); }
    finally { setBusy(false); }
  }

  async function confirmCustomerImport() {
    setBusy(true); setNotice("");
    try {
      const result = await requestCustomerImport("");
      setCustomers((current) => [...current, ...result.customers].sort((a, b) => a.name.localeCompare(b.name)));
      setImportFile(null); setImportImages([]); setImportPreview(null); setImportOpen(false);
      setNotice(t(`已导入 ${result.imported_count} 位客户。`, `Imported ${result.imported_count} customers.`));
    } catch (error) { setNotice(error instanceof Error ? error.message : t("客户导入失败", "Customer import failed")); }
    finally { setBusy(false); }
  }

  async function downloadImportTemplate(format: "csv" | "xlsx") {
    setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/customers/import/template?format=${format}`, { credentials: "include", cache: "no-store" });
      if (!response.ok) throw new Error(t("模板下载失败；请确认当前账号是工作区管理员", "Template download failed. Check that you are a workspace administrator."));
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `dealguard-customers-template.${format}`; link.click(); URL.revokeObjectURL(url);
    } catch (error) { setNotice(error instanceof Error ? error.message : t("模板下载失败", "Template download failed")); }
    finally { setBusy(false); }
  }

  return <div className="dg-operations">
    <WorkspaceNav role={role} />
    <main className="dg-operations-main">
      <header className="dg-operations-head"><div><h1>{t("客户", "Customers")}</h1><p>{t("工作区客户资料与服务等级。销售账号仅能查看分配给自己的客户。", "Manage workspace customer profiles and tiers. Sales users can only see assigned customers.")}</p></div>{role === "admin" && <div className="dg-operations-actions"><button className="dg-operations-secondary" type="button" onClick={() => { setFormOpen(false); setImportOpen((open) => !open); }}>{importOpen ? t("关闭导入", "Close import") : t("批量导入", "Bulk import")}</button><button className="dg-operations-primary" type="button" onClick={() => { setImportOpen(false); setFormOpen((open) => !open); }}>{formOpen ? t("取消", "Cancel") : t("新增客户", "Add customer")}</button></div>}</header>
      {notice && <div className="dg-operations-notice" role="status">{notice} {(notice.startsWith("请先登录") || notice.startsWith("Sign in")) && <Link href="/">{t("返回登录", "Back to sign in")}</Link>}</div>}
      {formOpen && role === "admin" && <section className="dg-operations-panel" aria-label={t("新增客户表单", "Add customer form")}><form className="dg-operations-form" onSubmit={addCustomer}>
        <label>{t("客户图片 / Logo（可选）", "Customer image / logo (optional)")}<input key={newCustomerImage?.name ?? "empty"} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setNewCustomerImage(event.target.files?.[0] ?? null)} /></label>
        <label className="dg-form-wide">{t("客户名称", "Customer name")}<input value={name} onChange={(event) => setName(event.target.value)} minLength={2} maxLength={160} placeholder={t("输入公司或客户名称", "Enter a company or customer name")} required /></label>
        <label>{t("客户等级", "Customer tier")}<select value={tier} onChange={(event) => setTier(event.target.value as Customer["tier"])}><option value="Standard">{localizedCustomerTier("Standard", locale)}</option><option value="VIP">VIP</option></select></label>
        <div className="dg-form-actions"><button className="dg-operations-secondary" type="button" onClick={() => setFormOpen(false)}>{t("取消", "Cancel")}</button><button className="dg-operations-primary" disabled={busy}>{busy ? t("正在保存…", "Saving…") : t("保存客户", "Save customer")}</button></div>
      </form></section>}
      {importOpen && role === "admin" && <section className="dg-operations-panel dg-product-import" aria-label={t("批量导入客户", "Bulk import customers")}>
        <div className="dg-product-import-head"><div><h2>{t("批量导入客户", "Bulk import customers")}</h2><p>{t("表格需包含 customer_id、name、tier 三列，客户等级为 VIP 或 Standard。", "The spreadsheet must contain customer_id, name, and tier. Tier must be VIP or Standard.")}</p><p className="dg-import-image-help">{t("可同时选择客户图片，图片文件名需与 customer_id 一致，例如 DG-C-001.png；每张最多 5 MiB，总计最多 30 MiB。", "Add customer images in the same import. Name each image after its customer_id, such as DG-C-001.png. Up to 5 MiB per image and 30 MiB total.")}</p></div><div className="dg-product-import-templates"><button type="button" className="dg-operations-secondary" disabled={busy} onClick={() => void downloadImportTemplate("csv")}>{t("下载 CSV 模板", "Download CSV template")}</button><button type="button" className="dg-operations-secondary" disabled={busy} onClick={() => void downloadImportTemplate("xlsx")}>{t("下载 Excel 模板", "Download Excel template")}</button></div></div>
        <form className="dg-product-import-form" onSubmit={previewCustomerImport}>
          <label>{t("选择客户表格", "Select customer spreadsheet")}<input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { setImportFile(event.target.files?.[0] ?? null); setImportPreview(null); }} /></label>
          <label>{t("选择客户图片（可选）", "Select customer images (optional)")}<input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={(event) => { setImportImages(Array.from(event.target.files ?? [])); setImportPreview(null); }} /></label>
          <button className="dg-operations-primary" type="submit" disabled={busy || !importFile}>{busy ? t("正在校验…", "Validating…") : t("预览并校验", "Preview and validate")}</button>
        </form>
        {importPreview && <div className="dg-product-import-preview">
          <div className="dg-product-import-summary"><strong>{importPreview.filename}</strong><span>{t(`${importPreview.total_rows} 行`, `${importPreview.total_rows} rows`)}</span><span className="dg-import-valid">{t("可导入", "Valid")}: {importPreview.valid_rows}</span><span className={importPreview.invalid_rows ? "dg-import-invalid" : "dg-import-valid"}>{t("需修正", "Needs correction")}: {importPreview.invalid_rows}</span></div>
          {importPreview.unmatched_images.length > 0 && <p className="dg-product-import-warning">{t("这些图片未匹配到可导入的客户编号，将被忽略：", "These images do not match an importable customer ID and will be ignored: ")}{importPreview.unmatched_images.join(", ")}</p>}
          {importPreview.rows.length > 0 && <div className="dg-quote-table-wrap"><table className="dg-product-import-table"><thead><tr><th>{t("行", "Row")}</th><th>{t("客户编号", "Customer ID")}</th><th>{t("客户名称", "Name")}</th><th>{t("等级", "Tier")}</th><th>{t("图片", "Image")}</th><th>{t("校验", "Validation")}</th></tr></thead><tbody>{importPreview.rows.slice(0, 50).map((row) => <tr key={row.row_number}><td>{row.row_number}</td><td>{row.customer_id ?? "—"}</td><td>{row.name ?? "—"}</td><td>{row.tier ?? "—"}</td><td>{row.image_filename ? <span className="dg-import-image-cell">{imagePreviews[row.image_filename] && <Image src={imagePreviews[row.image_filename]} width={38} height={38} unoptimized alt="" />}{row.image_filename}</span> : "—"}</td><td>{row.errors.length ? <span className="dg-import-invalid">{row.errors.join(" ")}</span> : <span className="dg-import-valid">{t("通过", "Valid")}</span>}</td></tr>)}</tbody></table></div>}
          <div className="dg-form-actions"><button type="button" className="dg-operations-primary" disabled={busy || !importPreview.valid_rows || !!importPreview.invalid_rows || !importFile} onClick={() => void confirmCustomerImport()}>{t("确认导入", "Import")} {importPreview.valid_rows} {t("位客户", "customers")}</button></div>
        </div>}
      </section>}
      <div className="dg-operations-toolbar"><input className="dg-operations-search" aria-label={t("搜索客户", "Search customers")} placeholder={t("搜索客户名称或客户编号", "Search by customer name or ID")} value={search} onChange={(event) => setSearch(event.target.value)} /></div>
      <section className="dg-tier-overview" aria-label={t("客户等级分布", "Customer tier distribution")}><div className="dg-tier-overview-head"><div><h2>{t("客户结构", "Customer mix")}</h2><p>{t("按工作区客户等级汇总", "Workspace customers by tier")}</p></div><strong>{customers.length}<small> {t("位客户", "customers")}</small></strong></div><div className="dg-tier-stack" role="img" aria-label={`${t("VIP", "VIP")} ${customers.filter((item) => item.tier === "VIP").length}, ${localizedCustomerTier("Standard", locale)} ${customers.filter((item) => item.tier === "Standard").length}`}><i className="dg-tier-vip" style={{ width: `${customers.length ? customers.filter((item) => item.tier === "VIP").length / customers.length * 100 : 0}%` }} /><i className="dg-tier-standard" style={{ width: `${customers.length ? customers.filter((item) => item.tier === "Standard").length / customers.length * 100 : 0}%` }} /></div><div className="dg-tier-legend"><span><i className="dg-tier-dot-vip" />VIP <b>{customers.filter((item) => item.tier === "VIP").length}</b></span><span><i className="dg-tier-dot-standard" />{localizedCustomerTier("Standard", locale)} <b>{customers.filter((item) => item.tier === "Standard").length}</b></span></div></section>
      <section className="dg-operations-panel dg-catalog-panel"><div className="dg-quote-list-head"><strong>{t("客户列表", "Customers")}</strong><span>{visibleCustomers.length} {t("条", "items")}</span></div><div className="dg-quote-table-wrap"><table className="dg-catalog-table"><thead><tr><th>{t("客户编号", "Customer ID")}</th><th>{t("客户名称", "Customer")}</th><th>{t("图片", "Image")}</th><th>{t("客户等级", "Tier")}</th><th>{t("可用操作", "Actions")}</th></tr></thead><tbody>
        {visibleCustomers.map((customer) => <tr key={customer.id}><td className="dg-catalog-code">{customer.id}</td><td><strong>{customer.name}</strong></td><td><ImageField imageId={customer.image_id ?? null} alt={`${customer.name} logo`} label={t("上传客户 Logo", "Upload customer logo")} uploadPath={`/api/v1/customers/${encodeURIComponent(customer.id)}/image`} removePath={`/api/v1/customers/${encodeURIComponent(customer.id)}/image`} editable={role === "admin"} onChange={(imageId) => setCustomers((current) => current.map((item) => item.id === customer.id ? { ...item, image_id: imageId } : item))} /></td><td>{localizedCustomerTier(customer.tier, locale)}</td><td><button type="button" className="dg-operations-secondary" onClick={() => { window.location.href = `/?customer=${encodeURIComponent(customer.id)}`; }}>{t("创建询盘", "Start inquiry")}</button></td></tr>)}
      </tbody></table>{!visibleCustomers.length && <div className="dg-operations-empty">{t("没有匹配客户。", "No matching customers.")}{role === "admin" ? t("可添加客户后再创建询盘。", " Add a customer before starting an inquiry.") : t("请联系管理员维护客户资料。", " Contact an administrator to manage customer records.")}</div>}</div></section>
    </main>
  </div>;
}
