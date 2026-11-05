from collections.abc import Callable

from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import AppError
from app.core.permissions import Permission, has_permission
from app.core.security import decode_access_token
from app.models import User

AUTH_COOKIE = "recon_session"


def _unauthenticated() -> AppError:
    return AppError(401, "not_authenticated", "Please sign in.")


def get_current_user(
    token: str | None = Cookie(default=None, alias=AUTH_COOKIE),
    db: Session = Depends(get_db),
) -> User:
    """Reads the user from the database on every request.

    A deactivated user or a signed-out token therefore stops working immediately,
    and a role change applies at once.
    """
    if not token:
        raise _unauthenticated()
    claims = decode_access_token(token)
    if claims is None or not str(claims["sub"]).isdigit():
        raise _unauthenticated()
    user = db.get(User, int(claims["sub"]))
    if user is None or not user.is_active or user.token_version != claims["ver"]:
        raise _unauthenticated()
    return user


def require(permission: Permission) -> Callable[..., User]:
    """Route dependency that enforces a permission on the server."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise AppError(403, "forbidden", "Your role doesn't allow this action.")
        return user

    return checker
