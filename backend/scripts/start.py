"""Production entry point: migrate, then serve.

Render's free tier has no pre-deploy command or shell, so migrations must run at
startup. The advisory lock in alembic/env.py protects against concurrent starts.
Connecting can fail briefly while Neon wakes, so that part is retried.

Failures print only the error type or a description. Driver messages can include the
host and username, and this output goes to the hosting platform's logs.
"""

import os
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402

ATTEMPTS = 5
RETRY_SECONDS = 5


def check_configuration() -> None:
    try:
        get_settings()
    except ValidationError as exc:
        details = " ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        raise SystemExit(f"Invalid configuration: {details}") from None


def migrate() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    for attempt in range(1, ATTEMPTS + 1):
        try:
            command.upgrade(config, "head")
            return
        except OperationalError as exc:
            kind = exc.orig.__class__.__name__ if exc.orig else exc.__class__.__name__
            if attempt == ATTEMPTS:
                raise SystemExit(
                    f"Couldn't reach the database after {ATTEMPTS} attempts ({kind})."
                ) from None
            print(f"Database not ready ({attempt}/{ATTEMPTS}): {kind}", flush=True)
            time.sleep(RETRY_SECONDS)


def main() -> None:
    check_configuration()
    migrate()
    port = os.environ.get("PORT", "8000")
    os.execvp(  # noqa: S606 - fixed program name, with a port from the platform
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",  # noqa: S104 - required for the platform's router to reach it
            "--port",
            port,
            "--proxy-headers",
        ],
    )


if __name__ == "__main__":
    main()
