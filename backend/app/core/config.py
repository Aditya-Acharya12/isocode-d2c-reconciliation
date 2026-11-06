import re
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parents[2]
PLACEHOLDER_SECRET = "replace-with-a-long-random-value"  # noqa: S105
_URL_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg://")
_POOLER_MARK = "-pooler"
_NEON_DOMAIN = ".neon.tech"


def normalise_database_url(url: str) -> str:
    """Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def database_url_problem(value: str) -> str | None:
    """What's wrong with a connection string, in words that never repeat any of it.

    Hosting dashboards store exactly what's pasted, so these are the usual slips.
    """
    if not value:
        return "is empty"
    if value != value.strip():
        return "has spaces or a line break around it"
    if value[0] in "\"'":
        return "is wrapped in quotes"
    if value.lower().startswith("psql"):
        return "is a psql command, not just the connection string"
    if re.match(r"[A-Za-z_]\w*\s*=", value):
        return "starts with a variable name"
    if value.startswith("<"):
        return "still contains a placeholder"
    if not value.startswith(_URL_SCHEMES):
        return "doesn't start with postgresql://"
    try:
        url = make_url(value)
    except (ArgumentError, ValueError):
        return "can't be parsed as a connection string"
    if not url.host:
        return "has no host"
    if not url.database:
        return "has no database name"
    return None


def _host(value: str) -> str:
    return make_url(value).host or ""


def production_database_problems(pooled: str, direct: str) -> list[str]:
    """Both URLs must be valid, and each must point the right way."""
    problems = []
    pooled_problem = database_url_problem(pooled)
    direct_problem = database_url_problem(direct)
    if pooled_problem:
        problems.append(f"DATABASE_URL {pooled_problem}.")
    if direct_problem:
        reason = " Production needs it for migrations and jobs." if not direct else ""
        problems.append(f"DATABASE_URL_DIRECT {direct_problem}.{reason}")
    if problems:
        return problems

    pooled_host, direct_host = _host(pooled), _host(direct)
    direct_is_pooled = _POOLER_MARK in direct_host
    pooled_is_direct = pooled_host.endswith(_NEON_DOMAIN) and _POOLER_MARK not in pooled_host
    if direct_is_pooled and pooled_is_direct:
        return ["DATABASE_URL and DATABASE_URL_DIRECT look swapped."]
    if pooled == direct:
        problems.append("DATABASE_URL and DATABASE_URL_DIRECT are the same connection string.")
    if direct_is_pooled:
        problems.append(
            "DATABASE_URL_DIRECT points at a pooled host. "
            "Migrations and jobs need the direct connection."
        )
    if pooled_is_direct:
        problems.append(
            "DATABASE_URL points at a direct Neon host. "
            "API requests should use the pooled connection."
        )
    return problems


class Settings(BaseSettings):
    # Stops validation errors echoing the settings, which include connection strings.
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", extra="ignore", hide_input_in_errors=True
    )

    app_env: str = "development"
    database_url: str
    database_url_direct: str = ""
    jwt_secret: str
    access_token_minutes: int = 480
    cookie_secure: bool = False
    max_upload_mb: int = 25
    enable_diagnostics: bool = False
    demo_mode: bool = True
    max_import_rows: int = 200_000
    enable_job_worker: bool = True
    # Optional. Without a key, mapping suggestions come from the rule-based matcher.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    @property
    def sqlalchemy_url(self) -> str:
        return normalise_database_url(self.database_url)

    @property
    def sqlalchemy_direct_url(self) -> str:
        # The fallback suits local development, where there's one connection.
        # Validation refuses an empty direct URL in production, so it can't happen there.
        return normalise_database_url(self.database_url_direct or self.database_url)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @model_validator(mode="after")
    def refuse_unsafe_configuration(self) -> "Settings":
        if self.jwt_secret == PLACEHOLDER_SECRET or len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be a random value of at least 32 characters")
        if self.is_production and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if self.is_production:
            problems = production_database_problems(self.database_url, self.database_url_direct)
        else:
            problems = [
                f"{name} {problem}."
                for name, value in (
                    ("DATABASE_URL", self.database_url),
                    ("DATABASE_URL_DIRECT", self.database_url_direct),
                )
                if value or name == "DATABASE_URL"
                if (problem := database_url_problem(value))
            ]
        if problems:
            raise ValueError(" ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
