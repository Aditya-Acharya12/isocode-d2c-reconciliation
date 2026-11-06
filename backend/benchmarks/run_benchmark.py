"""The reproducible ingestion benchmark. Measures real job code against real Postgres.

    python benchmarks/run_benchmark.py                 # 100,000 rows, every file type
    python benchmarks/run_benchmark.py --rows 5000     # a quick check

It creates and uses its own database (recon_scratch_benchmark) on the local Docker
Postgres, dropping any earlier copy. It never touches the app's data. Generated files
go to the system temp folder, and results go to benchmarks/results.

This runs the job code in-process. It doesn't include HTTP overhead, and says nothing
about performance on any hosted environment.
"""

import argparse
import gc
import json
import os
import platform
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BENCH_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

ADMIN_URL = "postgresql+psycopg://recon:recon_local_only@localhost:5433/recon"
BENCH_URL = "postgresql://recon:recon_local_only@localhost:5433/recon_scratch_benchmark"
os.environ.update(
    DATABASE_URL=BENCH_URL,
    DATABASE_URL_DIRECT="",
    JWT_SECRET="benchmark-only-secret-that-is-long-enough",  # noqa: S106
    APP_ENV="benchmark",
    ENABLE_JOB_WORKER="false",
    MAX_IMPORT_ROWS="1000000",
    MAX_UPLOAD_MB="500",
    GEMINI_API_KEY="",
)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psutil  # noqa: E402
import xlsxwriter  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.database import get_direct_engine  # noqa: E402
from app.core.permissions import Role  # noqa: E402
from app.models import (  # noqa: E402
    BackgroundJob,
    ImportJob,
    Partner,
    SalesChannel,
    UploadedFile,
    User,
)
from app.models.enums import FileType, PartnerType  # noqa: E402
from app.services.ingestion import service  # noqa: E402
from app.services.ingestion.spec import MappingSpec  # noqa: E402
from app.services.ingestion.suggest import build_draft  # noqa: E402
from app.services.jobs import run_until_idle  # noqa: E402

# The system temp folder, so that a synced folder doesn't upload and rescan these files.
DATA_DIR = Path(tempfile.gettempdir()) / "recon-benchmark-data"
RESULTS_DIR = BENCH_DIR / "results"
SEED = 20261106
BAD_ROW_RATE = 0.005  # about one row in 200 has a problem, as real exports do


def _dates(rng, count, start="2026-07-01", days=120, fmt="%d/%m/%Y") -> np.ndarray:
    offsets = rng.integers(0, days, count)
    base = pd.Timestamp(start) + pd.to_timedelta(offsets, unit="D")
    return base.strftime(fmt).to_numpy()


def _money(rng, count, low, high) -> np.ndarray:
    return np.char.mod("%.2f", rng.uniform(low, high, count))


def _ids(prefix: str, count: int, offset: int = 0) -> np.ndarray:
    return np.char.add(prefix, (np.arange(count) + offset).astype(str))


def _break_rows(frame: pd.DataFrame, rng, amount_column: str, date_column: str) -> pd.DataFrame:
    """Plants realistic problems, so that the error paths are measured too."""
    count = len(frame)
    bad = rng.choice(count, int(count * BAD_ROW_RATE), replace=False)
    half = len(bad) // 2
    amounts = frame[amount_column].astype(object).to_numpy(copy=True)
    dates = frame[date_column].astype(object).to_numpy(copy=True)
    amounts[bad[:half]] = "not-a-number"
    dates[bad[half:]] = "31/02/2026"
    return frame.assign(**{amount_column: amounts, date_column: dates})


def generate(rows: int, tag: str = "") -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    awb = _ids(f"AWB{tag}", rows)
    order = _ids(f"ORD{tag}-", rows)
    return {
        "order": _break_rows(pd.DataFrame({
            "Order No": order,
            "Order Date": _dates(rng, rows),
            "Order Value": _money(rng, rows, 299, 4999),
            "Payment Mode": rng.choice(["COD", "Prepaid"], rows),
            "Status": rng.choice(["Delivered", "RTO", "In Transit", "Cancelled"], rows,
                                 p=[0.8, 0.1, 0.08, 0.02]),
            "AWB No": awb,
            "Courier": rng.choice(["Courier Aggregator A", "Courier Aggregator B"], rows),
            "Delivered On": _dates(rng, rows, "2026-07-05"),
            "Weight Grams": rng.integers(200, 5000, rows).astype(str),
            "Wt_Slab": rng.choice(["0.5kg", "1kg", "2kg", "5kg"], rows),
            "Zone": rng.choice(["A", "B", "C", "D"], rows),
            "Qty": rng.integers(1, 4, rows).astype(str),
            "Customer City": rng.choice(["Bengaluru", "Mumbai", "Delhi", "Chennai"], rows),
        }), rng, "Order Value", "Order Date"),
        "settlement": _break_rows(pd.DataFrame({
            "Settlement ID": _ids(f"SET{tag}-", rows),
            "Sub Order ID": order,
            "Net Paid": _money(rng, rows, 200, 4000),
            "Settlement Date": _dates(rng, rows, "2026-08-01"),
            "Gross Amount": _money(rng, rows, 299, 4999),
            "Commission": _money(rng, rows, 20, 900),
            "Other Fees": _money(rng, rows, 0, 60),
            "TCS": _money(rng, rows, 1, 25),
            "TDS": _money(rng, rows, 0.1, 5),
            "UTR": _ids(f"UTR{tag}", rows),
        }), rng, "Net Paid", "Settlement Date"),
        "cod_remittance": _break_rows(pd.DataFrame({
            "AWB_No": awb,
            "COD_Collected": _money(rng, rows, 299, 4999),
            "Net_Rem": _money(rng, rows, 280, 4700),
            "Remittance Date": _dates(rng, rows, "2026-08-01"),
            "COD Fee": _money(rng, rows, 15, 60),
            "Remittance ID": _ids(f"REM{tag}-", rows),
            "UTR": _ids(f"UTRC{tag}", rows),
        }), rng, "Net_Rem", "Remittance Date"),
        "freight_invoice": _break_rows(pd.DataFrame({
            "Invoice No": np.char.add(f"INV{tag}-", (np.arange(rows) // 1000).astype(str)),
            "AWB No": awb,
            "Total Charge": _money(rng, rows, 40, 250),
            "Invoice Date": _dates(rng, rows, "2026-08-01"),
            "Shipment Type": rng.choice(["Forward", "RTO"], rows, p=[0.9, 0.1]),
            "Billed Weight": rng.integers(200, 5000, rows).astype(str),
            "Wt_Slab": rng.choice(["0.5kg", "1kg", "2kg", "5kg"], rows),
            "Zone": rng.choice(["A", "B", "C", "D"], rows),
            "Base Freight": _money(rng, rows, 30, 200),
            "COD Charge": _money(rng, rows, 0, 50),
            "GST": _money(rng, rows, 5, 45),
        }), rng, "Total Charge", "Invoice Date"),
        "return_record": _break_rows(pd.DataFrame({
            "AWB No": awb,
            "Return Type": rng.choice(["RTO", "Customer Return"], rows),
            "Return Date": _dates(rng, rows, "2026-08-01"),
            "Return Status": rng.choice(["Closed", "In Transit"], rows),
            "Quantity": rng.integers(1, 4, rows).astype(str),
            "Return Value": _money(rng, rows, 299, 4999),
        }), rng, "Return Value", "Return Date"),
        "warehouse_receipt": _break_rows(pd.DataFrame({
            "GRN No": _ids(f"GRN{tag}-", rows),
            "AWB No": awb,
            "Received On": _dates(rng, rows, "2026-08-01"),
            "Qty Received": rng.integers(1, 4, rows).astype(str),
            "Condition": rng.choice(["Good", "Damaged"], rows, p=[0.95, 0.05]),
        }), rng, "Qty Received", "Received On"),
    }


def write_xlsx(frame: pd.DataFrame, path: Path) -> None:
    book = xlsxwriter.Workbook(str(path), {"constant_memory": True})
    sheet = book.add_worksheet("Report")
    sheet.write_row(0, 0, list(frame.columns))
    for index, row in enumerate(frame.itertuples(index=False), start=1):
        sheet.write_row(index, 0, row)
    book.close()


class PeakMemory:
    """Samples this process's memory while a step runs."""

    def __init__(self) -> None:
        self._process = psutil.Process()
        self._stop = threading.Event()
        self.peak = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(0.02):
            self.peak = max(self.peak, self._process.memory_info().rss)

    def __enter__(self) -> "PeakMemory":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


@dataclass
class Result:
    name: str
    file_type: str
    file_format: str
    rows: int
    size_mb: float
    upload_s: float
    analysis_s: float
    validation_s: float
    import_s: float
    total_s: float
    imported: int
    rejected: int
    duplicates: int
    updated: int
    peak_rss_mb: float
    import_steps_ms: dict
    under_60s: bool


def prepare_database() -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS recon_scratch_benchmark WITH (FORCE)"))
        connection.execute(text("CREATE DATABASE recon_scratch_benchmark"))
    admin.dispose()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


def seed() -> tuple[int, dict[str, int]]:
    with Session(get_direct_engine()) as db:
        user = User(
            email="benchmark@example.invalid",
            full_name="Benchmark",
            password_hash="not-used",  # noqa: S106
            role=Role.FINANCE_EXECUTIVE,
        )
        partners = {
            "marketplace": Partner(name="Marketplace 1", type=PartnerType.MARKETPLACE,
                                   settlement_terms_days=15, return_grace_days=15),
            "courier": Partner(name="Courier Aggregator A", type=PartnerType.COURIER,
                               settlement_terms_days=10, return_grace_days=15),
            "courier_b": Partner(name="Courier Aggregator B", type=PartnerType.COURIER,
                                 settlement_terms_days=10, return_grace_days=15),
            "warehouse": Partner(name="Warehouse", type=PartnerType.WAREHOUSE,
                                 settlement_terms_days=0, return_grace_days=0),
        }
        channel = SalesChannel(code="WEB", name="Website")
        db.add_all([user, *partners.values(), channel])
        db.commit()
        return user.id, {**{k: p.id for k, p in partners.items()}, "channel": channel.id}


SOURCES = {
    "order": "channel",
    "settlement": "marketplace",
    "cod_remittance": "courier",
    "freight_invoice": "courier",
    "return_record": "courier",
    "warehouse_receipt": "warehouse",
}


def run_case(name, path: Path, file_type: str, user_id: int, sources: dict) -> Result:
    content = path.read_bytes()
    key = SOURCES[file_type]
    source = {"channel_id": sources[key]} if key == "channel" else {"partner_id": sources[key]}
    gc.collect()
    memory = PeakMemory()
    with memory, Session(get_direct_engine(), expire_on_commit=False) as db:
        user = db.get(User, user_id)

        started = time.perf_counter()
        file = service.store_upload(
            db,
            filename=path.name,
            content=content,
            file_type=FileType(file_type),
            client_sha256=None,
            user=user,
            is_synthetic=True,
            partner_id=source.get("partner_id"),
            channel_id=source.get("channel_id"),
        )
        upload_s = time.perf_counter() - started
        size_mb = len(content) / 1024 / 1024
        del content

        started = time.perf_counter()
        run_until_idle()
        analysis_s = time.perf_counter() - started
        db.expire_all()
        file = db.get(UploadedFile, file.id)

        draft = build_draft(FileType(file_type), file.detection)
        fields = {}
        for column in draft["columns"]:
            field = column["selected_field"]
            if field:
                details = draft["field_details"][field]
                fields[field] = {k: v for k, v in details.items() if v is not None}
        spec = MappingSpec(sheet=draft["sheet"], header_row=draft["header_row"], fields=fields)

        started = time.perf_counter()
        job = service.start_validation(db, file, spec, user)
        run_until_idle()
        validation_s = time.perf_counter() - started
        db.expire_all()
        job = db.get(BackgroundJob, job.id)
        if job.result is None:
            raise RuntimeError(f"Validation failed for {name}: {job.error}")

        started = time.perf_counter()
        import_job, _ = service.start_import(
            db, file, spec, job.id, job.result["rows_invalid"], "heuristic", None, user
        )
        run_until_idle()
        import_s = time.perf_counter() - started
        db.expire_all()
        import_job = db.get(ImportJob, import_job.id)
        background = db.get(BackgroundJob, import_job.background_job_id)
        if background.status != "succeeded":
            raise RuntimeError(f"Import failed for {name}: {background.error}")

    total = upload_s + analysis_s + validation_s + import_s
    return Result(
        name=name,
        file_type=file_type,
        file_format=path.suffix[1:],
        rows=import_job.rows_total,
        size_mb=round(size_mb, 1),
        upload_s=round(upload_s, 2),
        analysis_s=round(analysis_s, 2),
        validation_s=round(validation_s, 2),
        import_s=round(import_s, 2),
        total_s=round(total, 2),
        imported=import_job.rows_inserted,
        rejected=import_job.rows_rejected,
        duplicates=import_job.rows_duplicate,
        updated=import_job.rows_updated,
        peak_rss_mb=round(memory.peak / 1024 / 1024),
        import_steps_ms=import_job.timings_ms,
        under_60s=total < 60,
    )


def environment() -> dict:
    with get_direct_engine().connect() as connection:
        postgres = connection.execute(text("SHOW server_version")).scalar()
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 1024**3, 1),
        "os": platform.platform(),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "postgres": str(postgres),
        "postgres_host": "local Docker container",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--skip-xlsx", action="store_true")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)
    print(f"Generating {args.rows:,} rows per file type...", flush=True)
    paths: list[tuple[str, Path, str]] = []
    for file_type, frame in generate(args.rows).items():
        path = DATA_DIR / f"{file_type}_{args.rows}.csv"
        frame.to_csv(path, index=False)
        paths.append((f"{file_type} CSV", path, file_type))
    if not args.skip_xlsx:
        # Different IDs, so that this measures a real import and not duplicate handling.
        for file_type, frame in generate(args.rows, tag="X").items():
            if file_type in ("order", "settlement"):
                path = DATA_DIR / f"{file_type}_{args.rows}.xlsx"
                write_xlsx(frame, path)
                paths.append((f"{file_type} XLSX", path, file_type))
    # A second copy of the orders, with a changed column, measures the update path.
    update = generate(args.rows)["order"]
    update["Zone"] = "Z"
    update_path = DATA_DIR / f"order_update_{args.rows}.csv"
    update.to_csv(update_path, index=False)
    paths.append(("order CSV, updating existing", update_path, "order"))

    prepare_database()
    user_id, sources = seed()

    results = []
    for name, path, file_type in paths:
        print(f"Running {name}...", flush=True)
        result = run_case(name, path, file_type, user_id, sources)
        results.append(result)
        print(
            f"  {result.total_s:6.1f}s total (upload {result.upload_s}, analysis "
            f"{result.analysis_s}, validation {result.validation_s}, import {result.import_s}), "
            f"peak {result.peak_rss_mb} MB, imported {result.imported:,}, "
            f"updated {result.updated:,}, rejected {result.rejected:,}",
            flush=True,
        )

    with get_direct_engine().connect() as connection:
        size_mb = connection.scalar(
            text("SELECT pg_database_size(current_database()) / 1024 / 1024")
        )
    with Session(get_direct_engine()) as db:
        jobs = db.scalar(select(func.count()).select_from(BackgroundJob))
    report = {
        "environment": environment(),
        "rows_per_file": args.rows,
        "bad_row_rate": BAD_ROW_RATE,
        "database_size_mb_after": size_mb,
        "jobs_run": jobs,
        "results": [asdict(result) for result in results],
    }
    output = RESULTS_DIR / f"ingestion_{args.rows}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
