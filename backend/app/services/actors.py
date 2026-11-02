from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac, sha256
import hmac
import secrets

from fastapi import HTTPException, Request, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.db.models import AuthAccount, AuthSession, DemoActor

COOKIE_NAME = "dealguard_session"
SESSION_TTL = timedelta(hours=12)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    iterations = 310_000
    digest = pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        actual = pbkdf2_hmac(algorithm.removeprefix("pbkdf2_"), password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_session(db: Session, actor_id: str) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    row = AuthSession(actor_id=actor_id, token_hash=sha256(token.encode()).hexdigest(), expires_at=datetime.now(timezone.utc) + SESSION_TTL)
    db.add(row)
    db.commit()
    return token, row


def get_demo_actor(db: Session, actor_id: str) -> DemoActor:
    actor = db.get(DemoActor, actor_id)
    if actor is None:
        raise HTTPException(status_code=401, detail={"code": "UNKNOWN_DEMO_ACTOR", "message": "Unknown demo actor."})
    return actor


def get_current_actor(request: Request, db: Session = Depends(get_db)) -> DemoActor:
    demo_actor = request.headers.get("X-Demo-Actor")
    if settings.app_env != "production" and demo_actor:
        return get_demo_actor(db, demo_actor)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return get_session_actor(request, db)
    if settings.app_env != "production":
        return get_demo_actor(db, demo_actor or "sales_a")
    raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "Sign in to continue."})


def get_session_actor(request: Request, db: Session = Depends(get_db)) -> DemoActor:
    token = request.cookies.get(COOKIE_NAME)
    token_hash = sha256(token.encode()).hexdigest() if token else ""
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail={"code": "SESSION_INVALID", "message": "Sign in again to continue."})
    actor = db.get(DemoActor, session.actor_id)
    account = db.get(AuthAccount, session.actor_id)
    if actor is None or account is None or not account.is_active:
        raise HTTPException(status_code=401, detail={"code": "SESSION_INVALID", "message": "Sign in again to continue."})
    return actor


def actor_payload(actor: DemoActor) -> dict[str, str]:
    return {"actor_id": actor.id, "display_name": actor.display_name, "role": actor.role, "tenant_id": actor.tenant_id}
