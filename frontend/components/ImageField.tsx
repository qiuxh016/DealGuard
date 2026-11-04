"use client";

import { ChangeEvent, useState } from "react";
import Image from "next/image";
import { useLanguage } from "./LanguageProvider";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type Props = {
  imageId: string | null;
  alt: string;
  label: string;
  uploadPath: string;
  removePath?: string;
  editable?: boolean;
  onChange: (imageId: string | null) => void;
};

export function ImageField({ imageId, alt, label, uploadPath, removePath, editable = true, onChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [controlsOpen, setControlsOpen] = useState(false);
  const { t } = useLanguage();

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setNotice("");
    try {
      const body = new FormData();
      body.set("file", file);
      const response = await fetch(`${API}${uploadPath}`, { method: "POST", credentials: "include", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("图片上传失败", "Image upload failed"));
      onChange(payload.image_id);
      setNotice(t("图片已保存", "Image saved"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : t("图片上传失败", "Image upload failed"));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removePath) return;
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch(`${API}${removePath}`, { method: "DELETE", credentials: "include" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? t("图片移除失败", "Image removal failed"));
      onChange(null);
      setNotice(t("图片已移除", "Image removed"));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : t("图片移除失败", "Image removal failed"));
    } finally {
      setBusy(false);
    }
  }

  return <div className="dg-image-field">
    {imageId
      ? editable
        ? <button type="button" className="dg-image-trigger" onClick={() => setControlsOpen((open) => !open)} aria-expanded={controlsOpen} aria-label={t("图片操作", "Image actions")}><Image className="dg-image-preview" src={`${API}/api/v1/media/${encodeURIComponent(imageId)}`} alt={alt} width={72} height={72} unoptimized /></button>
        : <Image className="dg-image-preview" src={`${API}/api/v1/media/${encodeURIComponent(imageId)}`} alt={alt} width={72} height={72} unoptimized />
      : editable
        ? <button type="button" className="dg-image-trigger" onClick={() => setControlsOpen((open) => !open)} aria-expanded={controlsOpen} aria-label={t("添加图片", "Add image")}><span className="dg-image-placeholder">{alt.slice(0, 1)}</span></button>
        : <span className="dg-image-placeholder" aria-label={t("尚未设置图片", "No image set")}>{alt.slice(0, 1)}</span>}
    {editable && controlsOpen && <div className="dg-image-controls">
      <label className="dg-image-upload"><span>{busy ? t("处理中…", "Working…") : imageId ? t("更换图片", "Replace image") : label}</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void upload(event)} disabled={busy} /></label>
      {imageId && removePath && <button type="button" className="dg-image-remove" onClick={() => void remove()} disabled={busy}>{t("移除", "Remove")}</button>}
    </div>}
    {editable && controlsOpen && <small className="dg-image-spec">{t("PNG、JPEG 或 WebP，最大 5 MiB", "PNG, JPEG or WebP · 5 MiB max")}</small>}
    {notice && <small className="dg-image-notice" role="status">{notice}</small>}
  </div>;
}
