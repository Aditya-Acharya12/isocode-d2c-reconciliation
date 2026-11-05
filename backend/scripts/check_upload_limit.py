"""Measures how large an upload passes through a deployment intact.

The server's checksum must match the one sent, because a proxy can truncate a body
without returning an error. Needs ENABLE_DIAGNOSTICS=true on the target.

    python scripts/check_upload_limit.py https://your-app.vercel.app
"""

import hashlib
import os
import secrets
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

FINANCE_EMAIL = "finance@loomandfold.example"
SIZES_MB = [1, 4, 6, 11, 20, 24]
TIMEOUT_SECONDS = 180


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    base_url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:3000"
    password = os.environ.get("SEED_FINANCE_PASSWORD")
    if not password:
        print("Set SEED_FINANCE_PASSWORD.")
        return 2

    all_passed = True
    with httpx.Client(base_url=base_url, timeout=TIMEOUT_SECONDS) as client:
        started = time.perf_counter()
        login = client.post(
            "/api/v1/auth/login", json={"email": FINANCE_EMAIL, "password": password}
        )
        print(f"login: {login.status_code} in {time.perf_counter() - started:.2f}s")
        if login.status_code != 200:
            print(login.text[:300])
            return 1

        print(f"{'size':>6}  {'status':>6}  {'intact':>6}  {'received':>12}  {'seconds':>8}")
        for size_mb in SIZES_MB:
            body = secrets.token_bytes(size_mb * 1024 * 1024)
            expected = hashlib.sha256(body).hexdigest()
            started = time.perf_counter()
            try:
                response = client.post("/api/v1/diagnostics/upload-probe", content=body)
            except httpx.HTTPError as exc:
                print(f"{size_mb:>4}MB  failed: {exc.__class__.__name__}")
                all_passed = False
                continue
            elapsed = time.perf_counter() - started
            ok = response.status_code == 200
            data = response.json() if ok else {}
            intact = ok and data["sha256"] == expected and data["bytes_received"] == len(body)
            all_passed = all_passed and intact
            print(
                f"{size_mb:>4}MB  {response.status_code:>6}  {str(intact):>6}  "
                f"{data.get('bytes_received', '-'):>12}  {elapsed:>8.2f}"
            )
    print("RESULT:", "all sizes intact" if all_passed else "SOME SIZES FAILED")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
