"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandMark } from "../../components/BrandMark";
import { LanguageToggle, useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function ResetPasswordPage() {
  const { t } = useLanguage();
  const router = useRouter();
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const resetToken = new URLSearchParams(window.location.search).get("token") ?? "";
    if (resetToken) {
      setToken(resetToken);
      window.history.replaceState(null, "", "/reset-password");
    }
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/auth/reset-password`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法重置密码");
      setNotice("密码已更新，现有登录会话已退出。请使用新密码重新登录。");
      window.setTimeout(() => router.replace("/"), 1200);
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法重置密码"); }
    finally { setBusy(false); }
  }

  return <main className="dg-app dg-login-shell"><section className="dg-login-card">
    <div className="dg-login-language"><LanguageToggle /></div>
    <div className="dg-brand-mark"><BrandMark /></div>
    <h1>{t("重置 DealGuard 密码", "Reset your DealGuard password")}</h1>
    <p>{t("设置至少 12 个字符的新密码。重置后所有现有会话会失效。", "Choose a new password with at least 12 characters. All current sessions will be signed out.")}</p>
    {!token ? <div className="dg-login-form"><p role="status">{t("重置链接无效或已过期，请联系工作区管理员重新生成。", "This reset link is invalid or expired. Ask your workspace administrator for a new one.")}</p><Link href="/">{t("返回登录", "Back to sign in")}</Link></div> : <form onSubmit={submit} className="dg-login-form">
      <label>{t("新密码", "New password")}<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} maxLength={1024} required /><small>{t("至少 12 个字符", "At least 12 characters")}</small></label>
      <button className="dg-button" disabled={busy}>{busy ? t("正在更新…", "Updating…") : t("更新密码", "Update password")}</button>
      {notice && <p role="status">{notice}</p>}
    </form>}
  </section></main>;
}
