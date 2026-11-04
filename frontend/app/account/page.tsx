"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import "../workspace.css";
import "../operations.css";
import "./account.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Profile = { actor_id: string; display_name: string; role: string; tenant_id: string; email: string | null; workspace_name: string | null };
type Customer = { id: string; name: string; tier: string };
type Member = { actor_id: string; display_name: string; email: string; role: string; customer_id: string | null; is_active: boolean };
type Invitation = { id: string; email: string; role: string; customer_id: string | null; expires_at: string; expired: boolean };
type ApiError = { detail?: { message?: string } | string };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, credentials: "include", cache: "no-store", headers: { "Content-Type": "application/json", ...init?.headers } });
  const payload = await response.json().catch(() => ({})) as T & ApiError;
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message ?? `请求失败 (${response.status})`);
  }
  return payload;
}

const roleName = (role: string) => role === "admin" ? "管理员" : role === "manager" ? "经理" : "销售";

export default function AccountPage() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"sales" | "manager">("sales");
  const [inviteCustomer, setInviteCustomer] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [actionLink, setActionLink] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadTeam() {
    const [memberRows, invitationRows, customerRows] = await Promise.all([
      api<Member[]>("/api/v1/auth/members"),
      api<Invitation[]>("/api/v1/auth/invitations"),
      api<Customer[]>("/api/v1/customers"),
    ]);
    setMembers(memberRows); setInvitations(invitationRows); setCustomers(customerRows);
  }

  useEffect(() => {
    void api<Profile>("/api/v1/auth/me").then(async (result) => {
      setProfile(result); setDisplayName(result.display_name);
      if (result.role === "admin") await loadTeam();
    }).catch((error: unknown) => setNotice(error instanceof Error ? error.message : "无法读取账户信息"));
  }, []);

  async function saveProfile(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice("");
    try {
      const updated = await api<Pick<Profile, "actor_id" | "display_name" | "role" | "tenant_id">>("/api/v1/auth/me", {
        method: "PATCH", body: JSON.stringify({ display_name: displayName }),
      });
      setProfile((current) => current ? { ...current, ...updated } : current);
      setMembers((current) => current.map((member) => member.actor_id === updated.actor_id ? { ...member, display_name: updated.display_name } : member));
      setNotice("个人资料已更新。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "更新失败"); }
    finally { setBusy(false); }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault(); setNotice("");
    if (newPassword !== confirmPassword) { setNotice("两次输入的新密码不一致。"); return; }
    setBusy(true);
    try {
      await api("/api/v1/auth/change-password", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword("");
      setNotice("密码已修改，当前设备保持登录；其他登录会话已退出。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "密码修改失败"); }
    finally { setBusy(false); }
  }

  async function inviteMember(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(""); setActionLink("");
    try {
      const invitation = await api<{ token: string; email: string }>("/api/v1/auth/invitations", {
        method: "POST", body: JSON.stringify({ email: inviteEmail, role: inviteRole, customer_id: inviteRole === "sales" ? inviteCustomer : null }),
      });
      setActionLink(`${window.location.origin}/join?token=${encodeURIComponent(invitation.token)}`);
      setInviteEmail(""); await loadTeam(); setNotice(`已生成 ${invitation.email} 的 48 小时邀请链接，请安全发送给对方。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "邀请创建失败"); }
    finally { setBusy(false); }
  }

  async function toggleMember(member: Member) {
    if (!profile || member.actor_id === profile.actor_id) return;
    setBusy(true); setNotice("");
    try {
      await api(`/api/v1/auth/members/${member.actor_id}`, { method: "PATCH", body: JSON.stringify({ is_active: !member.is_active }) });
      await loadTeam(); setNotice(`${member.display_name} 的账号已${member.is_active ? "停用" : "重新启用"}。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "成员更新失败"); }
    finally { setBusy(false); }
  }

  async function resetMemberPassword(member: Member) {
    setBusy(true); setNotice(""); setActionLink("");
    try {
      const result = await api<{ token: string }>(`/api/v1/auth/members/${member.actor_id}/password-reset`, { method: "POST" });
      setActionLink(`${window.location.origin}/reset-password?token=${encodeURIComponent(result.token)}`);
      setNotice(`已生成 ${member.display_name} 的 1 小时密码重置链接，请安全发送。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "重置链接生成失败"); }
    finally { setBusy(false); }
  }

  async function revokeInvitation(invitation: Invitation) {
    setBusy(true); setNotice("");
    try {
      await api(`/api/v1/auth/invitations/${invitation.id}`, { method: "DELETE" });
      await loadTeam(); setNotice(`已撤销 ${invitation.email} 的邀请。`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "撤销邀请失败"); }
    finally { setBusy(false); }
  }

  async function copyActionLink() {
    try { await navigator.clipboard.writeText(actionLink); setNotice("链接已复制。请通过安全渠道发送。"); }
    catch { setNotice("无法自动复制，请手动复制下方链接并通过安全渠道发送。"); }
  }

  return <div className="dg-operations">
    <WorkspaceNav role={profile?.role} />
    <main className="dg-operations-main dg-account-main">
      <header className="dg-operations-head"><div><h1>账户与团队</h1><p>维护个人资料与登录凭据；管理员可在此管理工作区成员和邀请。</p></div></header>
      {notice && <div className="dg-operations-notice" role="status">{notice}{notice.startsWith("Sign in") && <> <Link href="/">返回登录</Link></>}</div>}
      {!profile ? <section className="dg-operations-panel dg-account-loading">正在读取账户信息…</section> : <>
        <div className="dg-account-grid">
          <section className="dg-operations-panel dg-account-panel">
            <div className="dg-account-panel-head"><div><h2>个人资料</h2><p>当前工作区的身份信息</p></div></div>
            <form className="dg-account-form" onSubmit={saveProfile}>
              <label>姓名<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={2} maxLength={120} required /></label>
              <label>邮箱<input value={profile.email ?? "未设置"} readOnly aria-describedby="account-email-note" /></label>
              <small id="account-email-note">邮箱暂不支持直接修改；如需更换，请联系工作区管理员。</small>
              <div className="dg-account-facts"><span>角色</span><strong>{roleName(profile.role)}</strong><span>工作区</span><strong>{profile.workspace_name ?? "—"}</strong></div>
              <button className="dg-operations-primary" disabled={busy}>{busy ? "正在保存…" : "保存资料"}</button>
            </form>
          </section>
          <section className="dg-operations-panel dg-account-panel">
            <div className="dg-account-panel-head"><div><h2>修改密码</h2><p>新密码至少 12 个字符</p></div></div>
            <form className="dg-account-form" onSubmit={changePassword}>
              <label>当前密码<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label>
              <label>新密码<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} maxLength={1024} autoComplete="new-password" required /></label>
              <label>确认新密码<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} minLength={12} maxLength={1024} autoComplete="new-password" required /></label>
              <p className="dg-account-security-note">修改成功后，其他设备上的登录会话将退出。</p>
              <button className="dg-operations-primary" disabled={busy}>{busy ? "正在更新…" : "更新密码"}</button>
            </form>
          </section>
        </div>

        {profile.role === "admin" && <section className="dg-operations-panel dg-account-team">
          <div className="dg-account-panel-head"><div><h2>团队成员</h2><p>{members.length} 位成员 · 管理邀请、账号状态与密码重置</p></div></div>
          <form className="dg-account-invite" onSubmit={inviteMember}>
            <label>员工邮箱<input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} maxLength={254} required /></label>
            <label>工作区角色<select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as "sales" | "manager")}><option value="sales">销售</option><option value="manager">经理</option></select></label>
            {inviteRole === "sales" && <label>负责客户<select value={inviteCustomer} onChange={(event) => setInviteCustomer(event.target.value)} required><option value="">选择客户</option>{customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}</select></label>}
            <button className="dg-operations-primary" disabled={busy || (inviteRole === "sales" && !customers.length)}>生成邀请链接</button>
          </form>
          {actionLink && <div className="dg-account-linkbox"><label>一次性链接<input value={actionLink} readOnly /></label><button type="button" className="dg-operations-secondary" onClick={() => void copyActionLink()}>复制链接</button></div>}
          <div className="dg-account-member-list">{members.map((member) => <article className="dg-account-member" key={member.actor_id}>
            <div className="dg-account-member-info"><strong>{member.display_name}</strong><span>{member.email}</span><small>{roleName(member.role)}{member.customer_id ? ` · ${customers.find((item) => item.id === member.customer_id)?.name ?? member.customer_id}` : ""}</small></div>
            <span className={`dg-account-status ${member.is_active ? "is-active" : ""}`}>{member.is_active ? "正常" : "已停用"}</span>
            <div className="dg-account-member-actions"><button type="button" className="dg-operations-secondary" onClick={() => void resetMemberPassword(member)} disabled={busy || !member.is_active}>重置密码</button>{member.actor_id !== profile.actor_id && <button type="button" className="dg-operations-secondary" onClick={() => void toggleMember(member)} disabled={busy}>{member.is_active ? "停用账号" : "重新启用"}</button>}</div>
          </article>)}</div>
          {invitations.length > 0 && <div className="dg-account-invitations"><h3>待接受邀请</h3>{invitations.map((invitation) => <div className="dg-account-invitation" key={invitation.id}><span><strong>{invitation.email}</strong><small>{roleName(invitation.role)} · {invitation.expired ? "已过期" : `到期 ${new Date(invitation.expires_at).toLocaleString()}`}</small></span><button type="button" className="dg-operations-secondary" onClick={() => void revokeInvitation(invitation)} disabled={busy}>撤销邀请</button></div>)}</div>}
        </section>}
      </>}
    </main>
  </div>;
}
