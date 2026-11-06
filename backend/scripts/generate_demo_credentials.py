"""Writes random demo passwords to a file outside the repository. Prints neither.

Never overwrites an existing file, so re-running can't lock out the demo users.

    python scripts/generate_demo_credentials.py C:\\secrets\\demo-credentials.env
"""

import secrets
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if path.exists():
        print("File already exists, and was left unchanged.")
        return 0
    path.write_text(
        f"SEED_FINANCE_PASSWORD={secrets.token_urlsafe(18)}\n"
        f"SEED_FOUNDER_PASSWORD={secrets.token_urlsafe(18)}\n",
        encoding="ascii",
    )
    print("Created. Passwords weren't printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
