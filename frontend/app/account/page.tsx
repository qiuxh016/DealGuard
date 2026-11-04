"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { WorkspaceNav } from "../../components/WorkspaceNav";
import { ImageField } from "../../components/ImageField";
import { useLanguage } from "../../components/LanguageProvider";
import "../workspace.css";
import "../operations.css";
import "./account.css";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Profile = { actor_id: string; display_name: string; role: string; tenant_id: string; email: string | null; workspace_name: string | null; image_id: string | null };
type Customer = { id: string; name: string; tier: string };
type Member = { actor_id: string; display_name: string; email: string; role: string; customer_id: string | null; is_active: boolean };
type WorkspaceAdmin = { display_name: string; email: string };
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

export default function AccountPage() {
  const { t } = useLanguage();
  const roleName = (role: string) => role === "admin" ? t("管理员", "Administrator") : role === "manager" ? t("经理", "Manager") : t("销售", "Sales");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [workspaceAdmins, setWorkspaceAdmins] = useState<WorkspaceAdmin[]>([]);
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
      else setWorkspaceAdmins(await api<WorkspaceAdmin[]>("/api/v1/auth/workspace-admins"));
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
      <header className="dg-operations-head"><div><h1>{t("账户与团队", "Account and team")}</h1><p>{t("维护个人资料与登录凭据；管理员可在此管理工作区成员和邀请。", "Manage your profile and credentials. Administrators can manage workspace members and invitations here.")}</p></div></header>
      {notice && <div className="dg-operations-notice" role="status">{notice}{notice.startsWith("Sign in") && <> <Link href="/">{t("返回登录", "Back to sign in")}</Link></>}</div>}
      {!profile ? <section className="dg-operations-panel dg-account-loading">{t("正在读取账户信息…", "Loading account…")}</section> : <>
        <div className="dg-account-grid">
          <section className="dg-operations-panel dg-account-panel">
            <div className="dg-account-panel-head"><div><h2>{t("个人资料", "Profile")}</h2><p>{t("当前工作区的身份信息", "Identity in this workspace")}</p></div></div>
            <form className="dg-account-form" onSubmit={saveProfile}>
              <div className="dg-account-image-field"><ImageField imageId={profile.image_id ?? null} alt={t("账户头像", "Account avatar")} label={t("设置头像", "Set avatar")} uploadPath="/api/v1/auth/me/image" removePath="/api/v1/auth/me/image" onChange={(imageId) => { setProfile((current) => current ? { ...current, image_id: imageId } : current); window.dispatchEvent(new CustomEvent("dealguard-profile-avatar", { detail: imageId })); }} /></div>
              <label>{t("姓名", "Name")}<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} minLength={2} maxLength={120} required /></label>
              <label>{t("邮箱", "Email")}<input value={profile.email ?? t("未设置", "Not set")} readOnly aria-describedby="account-email-note" /></label>
              <small id="account-email-note">{t("邮箱暂不支持直接修改；如需更换，请联系工作区管理员。", "Email cannot be changed here. Contact your workspace administrator to update it.")}</small>
              <div className="dg-account-facts"><span>{t("角色", "Role")}</span><strong>{roleName(profile.role)}</strong><span>{t("工作区", "Workspace")}</span><strong>{profile.workspace_name ?? "—"}</strong></div>
              <button className="dg-operations-primary" disabled={busy}>{busy ? t("正在保存…", "Saving…") : t("保存资料", "Save profile")}</button>
            </form>
          </section>
          <section className="dg-operations-panel dg-account-panel">
            <div className="dg-account-panel-head"><div><h2>{t("修改密码", "Change password")}</h2><p>{t("新密码至少 12 个字符", "New password must be at least 12 characters")}</p></div></div>
            <form className="dg-account-form" onSubmit={changePassword}>
              <label>{t("当前密码", "Current password")}<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label>
              <label>{t("新密码", "New password")}<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} maxLength={1024} autoComplete="new-password" required /></label>
              <label>{t("确认新密码", "Confirm new password")}<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} minLength={12} maxLength={1024} autoComplete="new-password" required /></label>
              <p className="dg-account-security-note">{t("修改成功后，其他设备上的登录会话将退出。", "Other sessions will be signed out after the password changes.")}</p>
              <button className="dg-operations-primary" disabled={busy}>{busy ? t("正在更新…", "Updating…") : t("更新密码", "Update password")}</button>
            </form>
          </section>
        </div>

        {profile.role !== "admin" && <section className="dg-operations-panel dg-account-team">
          <div className="dg-account-panel-head"><div><h2>{t("工作区管理员", "Workspace administrators")}</h2><p>{t("如需更换邮箱、调整客户归属或处理工作区设置，请联系以下管理员。", "Contact an administrator for account changes, customer assignment updates, or workspace settings.")}</p></div></div>
          {workspaceAdmins.length ? <div className="dg-account-member-list">{workspaceAdmins.map((admin) => <article className="dg-account-member dg-account-admin-contact" key={admin.email}>
            <div className="dg-account-member-info"><strong>{admin.display_name}</strong><span>{admin.email}</span><small>{roleName("admin")}</small></div>
          </article>)}</div> : <p className="dg-account-security-note">{t("当前工作区暂未配置可用管理员。", "No active workspace administrator is configured.")}</p>}
        </section>}

        {profile.role === "admin" && <section className="dg-operations-panel dg-account-team">
          <div className="dg-account-panel-head"><div><h2>{t("团队成员", "Team members")}</h2><p>{t(`${members.length} 位成员 · 管理邀请、账号状态与密码重置`, `${members.length} members · Manage invitations, account status, and password resets`)}</p></div></div>
          <form className="dg-account-invite" onSubmit={inviteMember}>
            <label>{t("员工邮箱", "Employee email")}<input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} maxLength={254} required /></label>
            <label>{t("工作区角色", "Workspace role")}<select value={inviteRole} onChange={(event) => setInviteRole(event.target.value as "sales" | "manager")}><option value="sales">{t("销售", "Sales")}</option><option value="manager">{t("经理", "Manager")}</option></select></label>
            {inviteRole === "sales" && <label>{t("负责客户", "Assigned customer")}<select value={inviteCustomer} onChange={(event) => setInviteCustomer(event.target.value)} required><option value="">{t("选择客户", "Select a customer")}</option>{customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}</select></label>}
            <button className="dg-operations-primary" disabled={busy || (inviteRole === "sales" && !customers.length)}>{t("生成邀请链接", "Create invitation link")}</button>
          </form>
          {actionLink && <div className="dg-account-linkbox"><label>{t("一次性链接", "One-time link")}<input value={actionLink} readOnly /></label><button type="button" className="dg-operations-secondary" onClick={() => void copyActionLink()}>{t("复制链接", "Copy link")}</button></div>}
          <div className="dg-account-member-list">{members.map((member) => <article className="dg-account-member" key={member.actor_id}>
            <div className="dg-account-member-info"><strong>{member.display_name}</strong><span>{member.email}</span><small>{roleName(member.role)}{member.customer_id ? ` · ${customers.find((item) => item.id === member.customer_id)?.name ?? member.customer_id}` : ""}</small></div>
            <span className={`dg-account-status ${member.is_active ? "is-active" : ""}`}>{member.is_active ? t("正常", "Active") : t("已停用", "Disabled")}</span>
            <div className="dg-account-member-actions"><button type="button" className="dg-operations-secondary" onClick={() => void resetMemberPassword(member)} disabled={busy || !member.is_active}>{t("重置密码", "Reset password")}</button>{member.actor_id !== profile.actor_id && <button type="button" className="dg-operations-secondary" onClick={() => void toggleMember(member)} disabled={busy}>{member.is_active ? t("停用账号", "Disable account") : t("重新启用", "Reactivate")}</button>}</div>
          </article>)}</div>
          {invitations.length > 0 && <div className="dg-account-invitations"><h3>{t("待接受邀请", "Pending invitations")}</h3>{invitations.map((invitation) => <div className="dg-account-invitation" key={invitation.id}><span><strong>{invitation.email}</strong><small>{roleName(invitation.role)} · {invitation.expired ? t("已过期", "Expired") : t(`到期 ${new Date(invitation.expires_at).toLocaleString()}`, `Expires ${new Date(invitation.expires_at).toLocaleString("en-US")}`)}</small></span><button type="button" className="dg-operations-secondary" onClick={() => void revokeInvitation(invitation)} disabled={busy}>{t("撤销邀请", "Revoke invitation")}</button></div>)}</div>}
        </section>}
      </>}
    </main>
  </div>;
}
