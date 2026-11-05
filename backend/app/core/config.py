from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PLACEHOLDER_SECRET = "replace-with-a-long-random-value"  # noqa: S105


def normalise_database_url(url: str) -> str:
    """Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_env: str = "development"
    database_url: str
    database_url_direct: str = ""
    jwt_secret: str
    access_token_minutes: int = 480
    cookie_secure: bool = False
    max_upload_mb: int = 25
    enable_diagnostics: bool = False
    demo_mode: bool = True

    @property
    def sqlalchemy_url(self) -> str:
        return normalise_database_url(self.database_url)

    @property
    def sqlalchemy_direct_url(self) -> str:
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
