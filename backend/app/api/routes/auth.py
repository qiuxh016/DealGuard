from datetime import datetime, timezone
from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.db.models import AuthAccount, AuthSession, DemoActor
from app.services.actors import COOKIE_NAME, SESSION_TTL, actor_payload, get_session_actor, issue_session, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    actor = db.get(DemoActor, body.actor_id)
    account = db.get(AuthAccount, body.actor_id)
    if actor is None or account is None or not account.is_active or not verify_password(body.password, account.password_hash):
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "Actor ID or password is incorrect."})
    token, _ = issue_session(db, actor.id)
    response.set_cookie(COOKIE_NAME, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True,
                        secure=settings.app_env == "production", samesite="strict", path="/")
    return actor_payload(actor)


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
