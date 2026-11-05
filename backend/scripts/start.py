"""Production entry point: migrate, then serve.

Render's free tier has no pre-deploy command or shell, so migrations must run at
startup. The advisory lock in alembic/env.py protects against concurrent starts.
Connecting can fail briefly while Neon wakes, so that part is retried.
"""

import os
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError

BACKEND_DIR = Path(__file__).resolve().parents[1]
ATTEMPTS = 5
RETRY_SECONDS = 5


def migrate() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    for attempt in range(1, ATTEMPTS + 1):
        try:
            command.upgrade(config, "head")
            return
        except OperationalError as exc:
            if attempt == ATTEMPTS:
                raise
            print(f"Database not ready ({attempt}/{ATTEMPTS}): {exc.__class__.__name__}")
            time.sleep(RETRY_SECONDS)


def main() -> None:
    sys.path.insert(0, str(BACKEND_DIR))
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
