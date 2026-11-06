"""Runs one command with extra environment files, and redacts secrets from its output.

Lets deployment credentials live outside the repository (and outside any synced
folder) without the application knowing where they are. The app itself only ever
reads ordinary environment variables, so no path reaches production settings.

    python scripts/with_env.py --env-file C:\\secrets\\.env.deploy -- python -m alembic current

Later files override earlier ones, and all of them override backend/.env.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

SENSITIVE_KEY_PARTS = ("URL", "PASSWORD", "SECRET", "TOKEN", "KEY")
MASK = "[redacted]"


def sensitive_strings(values: dict[str, str]) -> list[str]:
    """Each secret value, plus the parts of a connection string that could leak alone."""
    found: set[str] = set()
    for key, value in values.items():
        if not value or not any(part in key.upper() for part in SENSITIVE_KEY_PARTS):
            continue
        found.add(value)
        try:
            url = make_url(value)
        except ArgumentError:
            continue
        for part in (url.password, url.host, url.username):
            if part:
                found.add(str(part))
    # Longest first, so a whole URL is masked before the pieces inside it.
    return sorted(found, key=len, reverse=True)


def resolve_host(host: str, dns_server: str) -> str:
    """Looks a host up on a named DNS server, for when the local resolver fails."""
    result = subprocess.run(  # noqa: S603 - fixed program, developer-supplied arguments
        ["nslookup", host, dns_server],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
    )
    answer = result.stdout.split("Name:", 1)
    addresses = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", answer[1]) if len(answer) > 1 else []
    if not addresses:
        raise SystemExit(f"DNS server {dns_server} returned no address for a database host.")
    return addresses[0]


def pin_database_hosts(values: dict[str, str], dns_server: str) -> list[str]:
    """Adds hostaddr to database URLs, so the driver skips the local resolver.

    The hostname stays in the URL, because TLS and Neon's routing both depend on it.
    Returns the addresses used, so that they can be masked in the output too.
    """
    addresses = []
    for key, value in values.items():
        if not key.startswith("DATABASE_URL") or not value:
            continue
        url = make_url(value)
        address = resolve_host(url.host, dns_server)
        addresses.append(address)
        values[key] = url.update_query_dict({"hostaddr": address}).render_as_string(
            hide_password=False
        )
    return addresses


def redact(line: str, secrets_to_hide: list[str]) -> str:
    for secret in secrets_to_hide:
        line = line.replace(secret, MASK)
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", action="append", required=True)
    parser.add_argument("--cwd")
    parser.add_argument(
        "--dns",
        help="Resolve database hosts on this DNS server. For networks whose resolver fails.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given")

    values: dict[str, str] = {}
    for path in args.env_file:
        if not os.path.isfile(path):
            print(f"Environment file not found: {path}")
            return 2
        values.update({k: v for k, v in dotenv_values(path).items() if v is not None})

    hidden_values = dict(values)
    extra_hidden = pin_database_hosts(values, args.dns) if args.dns else []
    env = {**os.environ, **values, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    hidden = sorted(
        {*sensitive_strings(hidden_values), *sensitive_strings(values), *extra_hidden},
        key=len,
        reverse=True,
    )
    executable = shutil.which(command[0]) or command[0]
    process = subprocess.Popen(  # noqa: S603 - runs the developer's own command
        [executable, *command[1:]],
        cwd=args.cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in process.stdout:
        print(redact(line, hidden), end="", flush=True)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
