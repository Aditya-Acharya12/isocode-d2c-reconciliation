from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

# Neon suspends idle computes after 5 minutes, which kills open connections.
# pre_ping replaces dead ones, and recycling stops them being held for long.
_POOL_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 240,
    "pool_size": 3,
    "max_overflow": 2,
    "pool_timeout": 30,
}
_CONNECT_TIMEOUT_SECONDS = 15


@lru_cache
def get_engine() -> Engine:
    """Engine for API requests. Uses the pooled URL on Neon."""
    return create_engine(
        get_settings().sqlalchemy_url,
        # Transaction-mode pooling can hand each transaction a different server
        # connection, so server-side prepared statements are turned off.
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS, "prepare_threshold": None},
        **_POOL_OPTIONS,
    )


@lru_cache
def get_direct_engine() -> Engine:
    """Engine for session-level features: advisory locks, COPY and staging tables."""
    return create_engine(
        get_settings().sqlalchemy_direct_url,
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
        **_POOL_OPTIONS,
    )


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with _session_factory()() as session:
        yield session
