from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_ALGORITHM = "HS256"
_hasher = PasswordHasher()
# Verified when an email is unknown, so response time doesn't reveal which accounts exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Returns the claims, or None for any invalid, tampered or expired token."""
    try:
        return jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[_ALGORITHM],
            options={"require": ["sub", "ver", "exp"]},
        )
    except jwt.PyJWTError:
        return None
