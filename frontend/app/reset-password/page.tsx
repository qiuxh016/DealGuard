"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandMark } from "../../components/BrandMark";
import "../workspace.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function ResetPasswordPage() {
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
    <div className="dg-brand-mark"><BrandMark /></div>
    <h1>重置 DealGuard 密码</h1>
    <p>设置至少 12 个字符的新密码。重置后所有现有会话会失效。</p>
    {!token ? <div className="dg-login-form"><p role="status">重置链接无效或已过期，请联系工作区管理员重新生成。</p><Link href="/">返回登录</Link></div> : <form onSubmit={submit} className="dg-login-form">
      <label>新密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" minLength={12} maxLength={1024} required /><small>至少 12 个字符</small></label>
      <button className="dg-button" disabled={busy}>{busy ? "正在更新…" : "更新密码"}</button>
      {notice && <p role="status">{notice}</p>}
    </form>}
  </section></main>;
}
