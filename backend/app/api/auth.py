from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.config import Settings
from app.config.deps import get_current_user, get_settings_dep
from app.config.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.config.time import as_utc, utcnow
from app.db.session import get_db
from app.models import Brand, RefreshToken, User
from app.schemas.auth import LoginRequest, OkResponse, RefreshRequest, TokenResponse, UserOut
from app.services import audit

router = APIRouter()
_DUMMY_HASH = hash_password("not-a-real-password")  # equalises timing for unknown emails


def user_out(db: Session, user: User) -> UserOut:
    brand_id = db.scalar(select(Brand.id).order_by(Brand.id).limit(1))
    return UserOut(
        id=user.id, name=user.name, email=user.email, role=user.role, active=user.active, default_brand_id=brand_id
    )


def _issue(db: Session, settings: Settings, user: User) -> TokenResponse:
    refresh, claims = create_refresh_token(settings, user.id)
    db.add(
        RefreshToken(
            jti=claims["jti"],
            user_id=user.id,
            expires_at=utcnow() + timedelta(days=settings.refresh_token_days),
        )
    )
    return TokenResponse(
        access_token=create_access_token(settings, user.id),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
        user=user_out(db, user),
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> TokenResponse:
    limiter = request.app.state.login_limiter
    email = body.email.lower()
    key = f"{email}|{request.client.host if request.client else '-'}"
    if limiter.blocked(key):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.active:
        limiter.fail(key)
        audit.record(db, None, "auth.login_failed", email, "Incorrect email or password", actor_label=email)
        db.commit()
        raise HTTPException(401, "Incorrect email or password")
    limiter.reset(key)
    tokens = _issue(db, settings, user)
    audit.record(db, user, "auth.login", user.email)
    db.commit()
    return tokens


def _active_refresh_row(db: Session, settings: Settings, token: str) -> tuple[RefreshToken, User]:
    bad = HTTPException(401, "Invalid or expired refresh token")
    try:
        claims = decode_token(settings, token, "refresh")
    except jwt.PyJWTError as exc:
        raise bad from exc
    row = db.scalar(select(RefreshToken).where(RefreshToken.jti == claims["jti"]))
    if row is None or as_utc(row.expires_at) < utcnow():
        raise bad
    if row.revoked_at is not None:
        # A rotated-out token was presented again: treat as theft and revoke the whole family.
        for r in db.scalars(select(RefreshToken).where(RefreshToken.user_id == row.user_id)):
            r.revoked_at = r.revoked_at or utcnow()
        db.commit()
        raise bad
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        raise bad
    return row, user


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> TokenResponse:
    row, user = _active_refresh_row(db, settings, body.refresh_token)
    row.revoked_at = utcnow()
    tokens = _issue(db, settings, user)
    db.commit()
    return tokens


@router.post("/auth/logout", response_model=OkResponse)
def logout(
    body: RefreshRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)
) -> OkResponse:
    try:
        row, user = _active_refresh_row(db, settings, body.refresh_token)
    except HTTPException:
        return OkResponse()  # already invalid: nothing to revoke
    row.revoked_at = utcnow()
    audit.record(db, user, "auth.logout", user.email)
    db.commit()
    return OkResponse()


@router.get("/me", response_model=UserOut)
@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    return user_out(db, user)
