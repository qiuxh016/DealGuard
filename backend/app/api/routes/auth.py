from datetime import datetime, timedelta, timezone
from collections import deque
from hashlib import sha256
from time import monotonic
import re
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.db.models import AuthAccount, AuthSession, Customer, DemoActor, PasswordResetToken, Tenant, WorkspaceInvitation
from app.services.actors import COOKIE_NAME, SESSION_TTL, actor_payload, get_session_actor, hash_password, issue_session, verify_password
from app.services.actors import get_current_actor
from app.services.permissions import require_capability

router = APIRouter(prefix="/auth", tags=["authentication"])
_login_failures: dict[str, deque[float]] = {}


class LoginRequest(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    actor_id: str | None = Field(default=None, min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def require_login_identifier(self):
        if not (self.email or self.actor_id):
            raise ValueError("Email is required.")
        return self


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(min_length=2, max_length=160)
    display_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("organization_name", "display_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern="^(sales|manager)$")
    customer_id: str | None = Field(default=None, max_length=80)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @model_validator(mode="after")
    def validate_role_assignment(self):
        if self.role == "sales" and not self.customer_id:
            raise ValueError("Sales invitations must be assigned to a customer.")
        if self.role == "manager" and self.customer_id is not None:
            raise ValueError("Manager invitations cannot be assigned to a customer.")
        return self


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=128)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=1024)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank.")
        return value


class MemberStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=12, max_length=1024)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True,
                        secure=settings.app_env == "production", samesite="strict", path="/")


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    now = monotonic()
    window = max(1, settings.auth_login_window_seconds)
    client_host = request.client.host if request.client else "unknown"
    rate_key = f"ip:{client_host}"
    attempts = _login_failures.setdefault(rate_key, deque())
    while attempts and now - attempts[0] >= window:
        attempts.popleft()
    if len(attempts) >= max(1, settings.auth_login_max_attempts):
        retry_after = max(1, int(window - (now - attempts[0])))
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry_after)},
                            detail={"code": "LOGIN_RATE_LIMITED", "message": "Too many failed sign-in attempts. Try again shortly."})
    account = db.scalar(select(AuthAccount).where(AuthAccount.email == body.email.strip().lower())) if body.email else db.get(AuthAccount, body.actor_id)
    actor = db.get(DemoActor, account.actor_id) if account else None
    if actor is None or account is None or not account.is_active or not verify_password(body.password, account.password_hash):
        attempts.append(now)
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "Email or password is incorrect."})
    token, _ = issue_session(db, actor.id)
    _set_session_cookie(response, token)
    return actor_payload(actor)


@router.post("/register", status_code=201)
def register(body: RegistrationRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    now = monotonic()
    window = max(1, settings.auth_login_window_seconds)
    client_host = request.client.host if request.client else "unknown"
    rate_key = f"register:{client_host}"
    attempts = _login_failures.setdefault(rate_key, deque())
    while attempts and now - attempts[0] >= window:
        attempts.popleft()
    if len(attempts) >= max(1, settings.auth_login_max_attempts):
        retry_after = max(1, int(window - (now - attempts[0])))
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry_after)},
                            detail={"code": "REGISTRATION_RATE_LIMITED", "message": "Too many registration attempts. Try again shortly."})
    attempts.append(now)
    if db.scalar(select(AuthAccount.actor_id).where(AuthAccount.email == body.email)):
        raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_REGISTERED", "message": "An account with this email already exists."})
    tenant_id = str(uuid4())
    actor_id = str(uuid4())
    tenant = Tenant(id=tenant_id, name=body.organization_name)
    actor = DemoActor(id=actor_id, display_name=body.display_name, role="admin", customer_id=None, tenant_id=tenant_id)
    account = AuthAccount(actor_id=actor_id, email=body.email, password_hash=hash_password(body.password), is_active=True)
    db.add_all([tenant, actor, account])
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_REGISTERED", "message": "An account with this email already exists."}) from error
    token, _ = issue_session(db, actor_id)
    _set_session_cookie(response, token)
    return actor_payload(actor)


@router.get("/invitations")
def list_invitations(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "workspace.manage_members", code="ADMIN_REQUIRED", message="Only workspace administrators can manage members.")
    rows = db.scalars(select(WorkspaceInvitation).where(
        WorkspaceInvitation.tenant_id == actor.tenant_id,
        WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None),
    ).order_by(WorkspaceInvitation.created_at.desc())).all()
    now = datetime.now(timezone.utc)
    return [{"id": row.id, "email": row.email, "role": row.role, "customer_id": row.customer_id,
             "expires_at": row.expires_at, "expired": row.expires_at.replace(tzinfo=timezone.utc) <= now}
            for row in rows]


@router.get("/members")
def list_members(actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "workspace.manage_members", code="ADMIN_REQUIRED", message="Only workspace administrators can manage members.")
    rows = db.execute(select(DemoActor, AuthAccount).join(AuthAccount, AuthAccount.actor_id == DemoActor.id).where(
        DemoActor.tenant_id == actor.tenant_id,
    ).order_by(DemoActor.display_name)).all()
    return [{"actor_id": member.id, "display_name": member.display_name, "email": account.email,
             "role": member.role, "customer_id": member.customer_id, "is_active": account.is_active}
            for member, account in rows]


@router.patch("/members/{member_id}")
def update_member_status(member_id: str, body: MemberStatusUpdate,
                         actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "workspace.manage_members", code="ADMIN_REQUIRED", message="Only workspace administrators can manage members.")
    if member_id == actor.id and not body.is_active:
        raise HTTPException(status_code=409, detail={"code": "CANNOT_DISABLE_SELF", "message": "You cannot deactivate your own account."})
    member = db.scalar(select(DemoActor).where(DemoActor.id == member_id, DemoActor.tenant_id == actor.tenant_id))
    account = db.get(AuthAccount, member_id) if member else None
    if member is None or account is None:
        raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND", "message": "Workspace member not found."})
    account.is_active = body.is_active
    if not body.is_active:
        db.execute(AuthSession.__table__.delete().where(AuthSession.actor_id == member_id))
        db.execute(update(PasswordResetToken).where(
            PasswordResetToken.actor_id == member_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.revoked_at.is_(None),
        ).values(revoked_at=datetime.now(timezone.utc)))
    db.commit()
    return {"actor_id": member.id, "is_active": account.is_active}


@router.post("/members/{member_id}/password-reset", status_code=201)
def create_password_reset(member_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "workspace.manage_members", code="ADMIN_REQUIRED", message="Only workspace administrators can manage members.")
    member = db.scalar(select(DemoActor).where(DemoActor.id == member_id, DemoActor.tenant_id == actor.tenant_id))
    account = db.get(AuthAccount, member_id) if member else None
    if member is None or account is None:
        raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND", "message": "Workspace member not found."})
    if not account.is_active:
        raise HTTPException(status_code=409, detail={"code": "MEMBER_INACTIVE", "message": "Enable this account before resetting its password."})
    now = datetime.now(timezone.utc)
    for existing in db.scalars(select(PasswordResetToken).where(
        PasswordResetToken.actor_id == member_id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.revoked_at.is_(None),
    )).all():
        existing.revoked_at = now
    raw_token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        actor_id=member_id, tenant_id=actor.tenant_id, token_hash=sha256(raw_token.encode()).hexdigest(),
        expires_at=now + timedelta(hours=1), requested_by=actor.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "expires_at": row.expires_at, "token": raw_token}


@router.post("/reset-password")
def reset_password(body: PasswordResetRequest, db: Session = Depends(get_db)):
    token_hash = sha256(body.token.encode()).hexdigest()
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.revoked_at is not None or row.expires_at.replace(tzinfo=timezone.utc) <= now:
        raise HTTPException(status_code=400, detail={"code": "PASSWORD_RESET_INVALID", "message": "This password reset link is invalid, expired, or has already been used."})
    account = db.get(AuthAccount, row.actor_id)
    member = db.scalar(select(DemoActor).where(DemoActor.id == row.actor_id, DemoActor.tenant_id == row.tenant_id))
    if account is None or member is None or not account.is_active:
        raise HTTPException(status_code=400, detail={"code": "PASSWORD_RESET_INVALID", "message": "This password reset link is invalid, expired, or has already been used."})
    claimed = db.execute(update(PasswordResetToken).where(
        PasswordResetToken.id == row.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.revoked_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).values(used_at=now))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=400, detail={"code": "PASSWORD_RESET_INVALID", "message": "This password reset link is invalid, expired, or has already been used."})
    account.password_hash = hash_password(body.password)
    for other in db.scalars(select(PasswordResetToken).where(
        PasswordResetToken.actor_id == row.actor_id,
        PasswordResetToken.id != row.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.revoked_at.is_(None),
    )).all():
        other.revoked_at = now
    db.execute(AuthSession.__table__.delete().where(AuthSession.actor_id == row.actor_id))
    db.commit()
    return {"status": "password_updated"}


@router.post("/invitations", status_code=201)
def create_invitation(body: InvitationCreate,
                      actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "workspace.manage_members", code="ADMIN_REQUIRED", message="Only workspace administrators can manage members.")
    if db.scalar(select(AuthAccount.actor_id).where(AuthAccount.email == body.email)):
        raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_REGISTERED", "message": "This email already belongs to an account."})
    now = datetime.now(timezone.utc)
    pending = db.scalars(select(WorkspaceInvitation).where(
        WorkspaceInvitation.tenant_id == actor.tenant_id,
        WorkspaceInvitation.email == body.email,
        WorkspaceInvitation.accepted_at.is_(None),
        WorkspaceInvitation.revoked_at.is_(None),
    )).all()
    if any(row.expires_at.replace(tzinfo=timezone.utc) > now for row in pending):
        raise HTTPException(status_code=409, detail={"code": "INVITATION_ALREADY_PENDING", "message": "An active invitation already exists for this email."})
    if body.role == "sales" and db.scalar(select(Customer.id).where(
        Customer.id == body.customer_id, Customer.tenant_id == actor.tenant_id,
    )) is None:
        raise HTTPException(status_code=422, detail={"code": "CUSTOMER_NOT_FOUND", "message": "Select a customer in this workspace for the sales account."})
    raw_token = secrets.token_urlsafe(32)
    row = WorkspaceInvitation(
        tenant_id=actor.tenant_id, email=body.email, role=body.role, customer_id=body.customer_id,
        token_hash=sha256(raw_token.encode()).hexdigest(), expires_at=now + timedelta(hours=48), created_by=actor.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "email": row.email, "role": row.role, "customer_id": row.customer_id,
            "expires_at": row.expires_at, "token": raw_token}


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: str, actor: DemoActor = Depends(get_current_actor), db: Session = Depends(get_db)):
    require_capability(actor, "workspace.manage_members", code="ADMIN_REQUIRED", message="Only workspace administrators can manage members.")
    row = db.scalar(select(WorkspaceInvitation).where(
        WorkspaceInvitation.id == invitation_id, WorkspaceInvitation.tenant_id == actor.tenant_id,
        WorkspaceInvitation.accepted_at.is_(None), WorkspaceInvitation.revoked_at.is_(None),
    ))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "INVITATION_NOT_FOUND", "message": "Invitation not found."})
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "revoked"}


@router.post("/accept-invitation", status_code=201)
def accept_invitation(body: InvitationAccept, response: Response, db: Session = Depends(get_db)):
    row = db.scalar(select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == sha256(body.token.encode()).hexdigest()))
    now = datetime.now(timezone.utc)
    if row is None or row.accepted_at is not None or row.revoked_at is not None or row.expires_at.replace(tzinfo=timezone.utc) <= now:
        raise HTTPException(status_code=400, detail={"code": "INVITATION_INVALID", "message": "This invitation is invalid, expired, or has already been used."})
    if db.scalar(select(AuthAccount.actor_id).where(AuthAccount.email == row.email)):
        raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_REGISTERED", "message": "This email already belongs to an account."})
    if row.role == "sales" and db.scalar(select(Customer.id).where(
        Customer.id == row.customer_id, Customer.tenant_id == row.tenant_id,
    )) is None:
        raise HTTPException(status_code=409, detail={"code": "INVITATION_CUSTOMER_UNAVAILABLE", "message": "The assigned customer is no longer available. Ask your administrator for a new invitation."})
    actor_id = str(uuid4())
    member = DemoActor(id=actor_id, display_name=body.display_name, role=row.role, customer_id=row.customer_id, tenant_id=row.tenant_id)
    account = AuthAccount(actor_id=actor_id, email=row.email, password_hash=hash_password(body.password), is_active=True)
    row.accepted_at = now
    db.add_all([member, account])
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "INVITATION_ALREADY_USED", "message": "This invitation has already been used."}) from error
    token, _ = issue_session(db, actor_id)
    _set_session_cookie(response, token)
    return actor_payload(member)


@router.get("/me")
def me(actor: DemoActor = Depends(get_session_actor)):
    return actor_payload(actor)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute(AuthSession.__table__.delete().where(AuthSession.token_hash == sha256(token.encode()).hexdigest()))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=settings.app_env == "production", samesite="strict")
    return {"status": "logged_out"}
