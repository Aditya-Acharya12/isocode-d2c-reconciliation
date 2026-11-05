"""Hosting checks, off unless ENABLE_DIAGNOSTICS is set.

The upload probe finds out how large a request can pass through the Vercel rewrite
before the real upload route depends on it. It reads and discards the body.
"""

import hashlib
import time

from fastapi import APIRouter, Depends, Request

from app.api.deps import require
from app.api.schemas import UploadProbeOut
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.permissions import Permission
from app.models import User

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.post("/upload-probe")
async def upload_probe(
    request: Request, _: User = Depends(require(Permission.OPERATE_RECONCILIATION))
) -> UploadProbeOut:
    settings = get_settings()
    if not settings.enable_diagnostics:
        raise AppError(404, "not_found", "Not found.")

    too_large = AppError(
        413, "file_too_large", f"The limit is {settings.max_upload_mb} MB."
    )
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
        raise too_large

    started = time.perf_counter()
    digest = hashlib.sha256()
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > settings.max_upload_bytes:
            raise too_large
        digest.update(chunk)
    return UploadProbeOut(
        bytes_received=received,
        sha256=digest.hexdigest(),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
