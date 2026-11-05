from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AUTH_COOKIE, get_current_user
from app.api.schemas import LoginRequest, SessionOut, UserOut
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import create_access_token, verify_password
from app.models import User
from app.services.audit import record_audit
from app.services.login_throttle import login_throttle

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_source(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _session(user: User) -> SessionOut:
    return SessionOut(
        user=UserOut.model_validate(user),
        permissions=sorted(ROLE_PERMISSIONS[user.role]),
        demo_mode=get_settings().demo_mode,
    )


def _clear_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        AUTH_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


@router.post("/login")
def login(
    body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> SessionOut:
    source = _client_source(request)
    if login_throttle.is_blocked(body.email, source):
        raise AppError(429, "too_many_attempts", "Too many attempts. Please wait a few minutes.")

    user = db.scalar(select(User).where(User.email == body.email))
    password_ok = verify_password(body.password, user.password_hash if user else None)

    if user is None or not password_ok or not user.is_active:
        login_throttle.record_failure(body.email, source)
        record_audit(
            db,
            actor_id=user.id if user else None,
            action="auth.login_failed",
            entity_type="user",
            entity_id=user.id if user else None,
            after={"reason": "invalid_credentials"},
        )
        db.commit()
        # The same message for every cause, so accounts can't be discovered here.
        raise AppError(401, "invalid_credentials", "Incorrect email or password.")

    login_throttle.clear_source(body.email, source)
    record_audit(
        db, actor_id=user.id, action="auth.login", entity_type="user", entity_id=user.id
    )
    db.commit()

    settings = get_settings()
    response.set_cookie(
        AUTH_COOKIE,
        create_access_token(user.id, user.token_version),
        max_age=settings.access_token_minutes * 60,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return _session(user)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    user.token_version += 1  # invalidates tokens already issued
    record_audit(
        db, actor_id=user.id, action="auth.logout", entity_type="user", entity_id=user.id
    )
    db.commit()
    _clear_cookie(response)


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> SessionOut:
    return _session(user)
