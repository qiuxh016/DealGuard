"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandMark } from "../../components/BrandMark";
import { LanguageToggle, useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function JoinWorkspacePage() {
  const { t } = useLanguage();
  const router = useRouter();
  const [token, setToken] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const inviteToken = new URLSearchParams(window.location.search).get("token") ?? "";
    if (inviteToken) {
      setToken(inviteToken);
      window.history.replaceState(null, "", "/join");
    }
  }, []);

  async function acceptInvitation(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/v1/auth/accept-invitation`, {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, display_name: displayName, password }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail?.message ?? "无法接受邀请");
      router.replace("/");
    } catch (error) { setNotice(error instanceof Error ? error.message : "无法接受邀请"); }
    finally { setBusy(false); }
  }

  return <main className="dg-app dg-login-shell"><section className="dg-login-card">
    <div className="dg-login-language"><LanguageToggle /></div>
    <div className="dg-brand-mark"><BrandMark /></div>
    <h1>{t("加入 DealGuard 工作区", "Join a DealGuard workspace")}</h1>
    <p>{t("设置姓名和密码后，即可使用管理员分配的工作区权限。", "Set your name and password to access the workspace assigned by your administrator.")}</p>
    {!token ? <div className="dg-login-form"><p role="status">{t("邀请链接无效或已过期，请联系工作区管理员重新发送。", "This invitation link is invalid or expired. Ask your workspace administrator to send a new one.")}</p><Link href="/">{t("返回登录", "Back to sign in")}</Link></div> : <form onSubmit={acceptInvitation} className="dg-login-form">
      <label>{t("姓名", "Name")}<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" minLength={2} maxLength={120} required /></label>
      <label>{t("设置密码", "Set password")}<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} maxLength={1024} required /><small>{t("至少 12 个字符", "At least 12 characters")}</small></label>
      <button className="dg-button" disabled={busy}>{busy ? t("正在加入…", "Joining…") : t("接受邀请并加入", "Accept invitation")}</button>
      {notice && <p role="status">{notice}</p>}
    </form>}
  </section></main>;
}
