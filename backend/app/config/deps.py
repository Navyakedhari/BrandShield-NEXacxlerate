import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config.config import Settings
from app.config.security import decode_token
from app.core.pipeline.url_safety import UrlChecker
from app.db.session import get_db
from app.models import User

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_url_checker(request: Request) -> UrlChecker:
    return request.app.state.url_checker


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    unauthorized = {"WWW-Authenticate": "Bearer"}
    if creds is None:
        raise HTTPException(401, "Not authenticated", headers=unauthorized)
    try:
        claims = decode_token(settings, creds.credentials, "access")
        user = db.get(User, int(claims["sub"]))
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(401, "Invalid or expired token", headers=unauthorized) from exc
    if user is None or not user.active:
        raise HTTPException(401, "Invalid or expired token", headers=unauthorized)
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You do not have permission to do this")
        return user

    return checker


# viewers: GET only. Reviews, brand edits, scans: analyst or admin.
require_writer = require_roles("admin", "analyst")
require_admin = require_roles("admin")
