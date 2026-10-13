import uuid
from datetime import timedelta
from typing import Any

import bcrypt
import jwt

from app.config.config import Settings
from app.config.time import utcnow

BCRYPT_ROUNDS = 12  # tests lower this for speed
MAX_PASSWORD_BYTES = 72  # bcrypt limit; longer passwords are rejected at the schema level


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except ValueError:
        return False


def _encode(settings: Settings, sub: int, kind: str, ttl: timedelta) -> tuple[str, dict]:
    now = utcnow()
    claims: dict[str, Any] = {
        "sub": str(sub),
        "typ": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm), claims


def create_access_token(settings: Settings, user_id: int) -> str:
    return _encode(settings, user_id, "access", timedelta(minutes=settings.access_token_minutes))[0]


def create_refresh_token(settings: Settings, user_id: int) -> tuple[str, dict]:
    return _encode(settings, user_id, "refresh", timedelta(days=settings.refresh_token_days))


def decode_token(settings: Settings, token: str, expected_type: str) -> dict:
    """Raises jwt.PyJWTError on any problem (bad signature, expired, wrong type)."""
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "sub", "typ", "jti"]},
    )
    if claims.get("typ") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return claims
