"""Validates rows and loads them in bulk. Loaded only by jobs, because pandas is heavy.

Everything here works on whole columns, with no per-row database writes. Money stays
as text until Postgres casts it to NUMERIC, so a float never touches an amount.
"""

import datetime as dt
import hashlib
import io
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Partner, SalesChannel
from app.models.enums import DeliveryStatus, FileType, IssueSeverity, PartnerType
from app.services.ingestion.canonical import (
    DATE,
    DATE_FORMATS,
    ENUM,
    ID,
    INT,
    MONEY,
    REF,
    Field,
    FileSchema,
    schema_for,
)
from app.services.ingestion.parsing import EXTRA_COLUMN, ROW_COLUMN, FileProblem, Table
from app.services.ingestion.spec import MappingSpec

ERROR, WARNING, DUPLICATE = (
    IssueSeverity.ERROR.value,
    IssueSeverity.WARNING.value,
    IssueSeverity.DUPLICATE.value,
)
ISSUE_COLUMNS = ["row_number", "field", "code", "severity", "message", "raw_value"]
MAX_RAW_VALUE = 200
MAX_STORED_ISSUES = 20_000
PREVIEW_ROWS = 20
PREVIEW_PROBLEM_ROWS = 10
MAX_ISSUE_GROUPS = 40
_NOT_AVAILABLE = {"-", "--", "na", "n/a", "nil", "null", "none"}
_SUMMARY_ROW = r"(?i)^(?:grand\s*|sub\s*)?totals?\b.*"
_MONEY_NOISE = r"(?i)₹|rs\.?|inr|,|\s"
_SCIENTIFIC = r"[+-]?\d+(?:\.\d+)?[eE][+-]?\d+"
_MAX_MONEY_DIGITS = 12
_MAX_INT = 2_000_000_000


@dataclass
class Validated:
    stage: pd.DataFrame  # the rows to load, in canonical form, keyed by field name
    issues: pd.DataFrame
    preview: list[dict]
    rows_total: int
    rows_valid: int
    rows_invalid: int
    rows_duplicate: int
    rows_excluded: int
    rows_warned: int


class _Issues:
    def __init__(self, rows: pd.Series) -> None:
        self.rows = rows
        self.frames: list[pd.DataFrame] = []
        self.error = pd.Series(False, index=rows.index)

    def add(self, mask, field: str | None, code: str, severity: str, message: str, raw=None):
        if not mask.any():
            return
        if severity == ERROR:
            self.error |= mask
        selected = self.rows[mask]
        raw_values = raw[mask].str.slice(0, MAX_RAW_VALUE) if raw is not None else None
        self.frames.append(
            pd.DataFrame(
                {
                    "row_number": selected.to_numpy(),
                    "field": field,
                    "code": code,
                    "severity": severity,
                    "message": message,
                    "raw_value": raw_values.to_numpy() if raw_values is not None else None,
                }
            )
        )

    def frame(self) -> pd.DataFrame:
        if not self.frames:
            return pd.DataFrame(columns=ISSUE_COLUMNS)
        return pd.concat(self.frames, ignore_index=True)


def _clean_money(raw: pd.Series) -> pd.Series:
    value = raw.str.replace(_MONEY_NOISE, "", regex=True)
    value = value.where(~value.str.casefold().isin(_NOT_AVAILABLE), "")
    in_brackets = value.str.fullmatch(r"\(.*\)")
    value = value.where(~in_brackets, "-" + value.str.strip("()"))
    trailing_minus = value.str.fullmatch(r"[\d.]+-")
    return value.where(~trailing_minus, "-" + value.str.rstrip("-"))


def _validate_money(raw: pd.Series, field: Field, mapping, issues: _Issues) -> pd.Series:
    value = _clean_money(raw)
    filled = value != ""
    numeric = value.str.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
    issues.add(
        filled & ~numeric,
        field.name,
        "invalid_amount",
        ERROR,
        f"{field.label} isn't a number. Expected a value like 1499.00.",
        raw,
    )
    good = filled & numeric
    digits = value.str.replace(r"^[+-]|\..*$", "", regex=True).str.len()
    issues.add(
        good & (digits > _MAX_MONEY_DIGITS),
        field.name,
        "amount_too_large",
        ERROR,
        f"{field.label} is too large to be a real amount.",
        raw,
    )
    issues.add(
        good & value.str.contains(r"\.\d{3,}$"),
        field.name,
        "amount_rounded",
        WARNING,
        f"{field.label} has more than two decimal places, and will be rounded to paise.",
        raw,
    )
    if mapping.negate:
        negative = value.str.startswith("-")
        value = value.where(~good, value.where(negative, "-" + value).where(~negative,
                            value.str.slice(1)))
    if not field.allow_negative:
        issues.add(
            good & value.str.startswith("-"),
            field.name,
            "negative_amount",
            ERROR,
            f"{field.label} can't be negative.",
            raw,
        )
    return value.where(good, "")


def _validate_date(raw: pd.Series, field: Field, mapping, issues: _Issues) -> pd.Series:
    pattern = DATE_FORMATS[mapping.date_format]
    parsed = pd.to_datetime(raw.where(raw != "", None), format=pattern, errors="coerce")
    filled = raw != ""
    issues.add(
        filled & parsed.isna(),
        field.name,
        "invalid_date",
        ERROR,
        f"{field.label} doesn't match the format {mapping.date_format}. "
        "Fix the value, or choose a different format.",
        raw,
    )
    too_old = parsed.dt.year < 2000
    issues.add(
        too_old.fillna(False).astype(bool),
        field.name,
        "date_out_of_range",
        ERROR,
        f"{field.label} is before the year 2000, so the date format may be wrong.",
        raw,
    )
    future = parsed > pd.Timestamp(dt.date.today() + dt.timedelta(days=1))
    issues.add(
        future.fillna(False).astype(bool),
        field.name,
        "date_in_future",
        WARNING,
        f"{field.label} is in the future. Check that the date format is right.",
        raw,
    )
    return parsed.dt.strftime("%Y-%m-%d").astype(object).where(parsed.notna() & ~too_old, "")


def _validate_identifier(raw: pd.Series, field: Field, issues: _Issues) -> pd.Series:
    corrupted = raw.str.fullmatch(_SCIENTIFIC)
    issues.add(
        corrupted,
        field.name,
        "corrupted_identifier",
        ERROR,
        f"{field.label} was turned into scientific notation by Excel, so digits are lost. "
        "Export it again with this column formatted as text.",
        raw,
    )
    return _validate_length(raw.where(~corrupted, ""), field, issues, raw)


def _validate_length(value: pd.Series, field: Field, issues: _Issues, raw: pd.Series) -> pd.Series:
    too_long = value.str.len() > field.max_length
    issues.add(
        too_long,
        field.name,
        "value_too_long",
        ERROR,
        f"{field.label} is longer than the {field.max_length} characters allowed.",
        raw,
    )
    return value.where(~too_long, "")


def _validate_int(raw: pd.Series, field: Field, issues: _Issues) -> pd.Series:
    filled = raw != ""
    whole = raw.str.fullmatch(r"\d+(?:\.0+)?")
    issues.add(
        filled & ~whole,
        field.name,
        "invalid_number",
        ERROR,
        f"{field.label} must be a whole number.",
        raw,
    )
    value = raw.str.replace(r"\.0+$", "", regex=True).where(filled & whole, "")
    too_big = value.str.len() > len(str(_MAX_INT))
    issues.add(too_big, field.name, "number_too_large", ERROR, f"{field.label} is too large.", raw)
    return value.where(~too_big, "")


def _validate_enum(raw: pd.Series, field: Field, mapping, issues: _Issues) -> pd.Series:
    lookup = {key.strip().casefold(): value for key, value in (mapping.value_map or {}).items()}
    value = raw.str.casefold().map(lookup).astype(object).where(lambda s: s.notna(), "")
    issues.add(
        (raw != "") & (value == ""),
        field.name,
        "unmapped_value",
        ERROR,
        f"This {field.label.lower()} isn't mapped to an allowed value. "
        "Map it on the mapping screen.",
        raw,
    )
    return value


def _validate_reference(raw: pd.Series, field: Field, couriers: dict[str, int], issues):
    value = raw.str.casefold().map(couriers).astype(object)
    unknown = (raw != "") & value.isna()
    issues.add(
        unknown,
        field.name,
        "unknown_courier",
        WARNING,
        f"This {field.label.lower()} isn't a known partner, so it won't be recorded. "
        "Add it under Partners if it should be.",
        raw,
    )
    return value.map(lambda item: "" if pd.isna(item) else str(int(item)))


def _dedupe_key(frame: pd.DataFrame, schema: FileSchema, file_type: FileType) -> pd.Series:
    """What makes two rows the same record. Identical content means the same transaction."""
    if file_type == FileType.ORDER:
        return frame["order_id"]
    if file_type == FileType.WAREHOUSE_RECEIPT:
        return frame["receipt_ref"]
    if file_type == FileType.FREIGHT_INVOICE:
        parts = ["invoice_no", "tracking_id", "shipment_type"]
        return frame[parts[0]].str.cat([frame[p] for p in parts[1:]], sep="\x1f")
    names = sorted(name for name in frame.columns if name != ROW_COLUMN)
    joined = frame[names[0]].str.cat([frame[name] for name in names[1:]], sep="\x1f")
    return pd.Series(
        [hashlib.sha256(value.encode()).hexdigest() for value in joined], index=frame.index
    )


def validate_rows(
    table: Table, spec: MappingSpec, file_type: FileType, couriers: dict[str, int]
) -> Validated:
    schema = schema_for(file_type)
    frame = table.frame
    missing = [m.column for m in spec.fields.values() if m.column not in table.columns]
    if missing:
        raise FileProblem(f"These mapped columns aren't in the file: {', '.join(missing)}.")

    rows = frame[ROW_COLUMN]
    issues = _Issues(rows)
    first_cells = [frame[column].str.strip() for column in table.columns[:3] if column in frame]
    summary = pd.Series(False, index=frame.index)
    for cells in first_cells:
        summary |= cells.str.fullmatch(_SUMMARY_ROW)
    issues.add(
        summary,
        None,
        "summary_row",
        WARNING,
        "This looks like a totals row, so it was left out to avoid counting amounts twice.",
    )
    extra = frame[EXTRA_COLUMN].str.strip() != ""
    issues.add(
        extra & ~summary,
        None,
        "too_many_columns",
        ERROR,
        "This row has more values than the header has columns, so its values may have shifted. "
        "A comma inside an unquoted value is the usual cause.",
        frame[EXTRA_COLUMN],
    )

    canonical: dict[str, pd.Series] = {}
    for field in schema.fields:
        mapping = spec.fields.get(field.name)
        if mapping is None:
            if field.default is not None:
                canonical[field.name] = pd.Series(field.default, index=frame.index, dtype=object)
            continue
        raw = frame[mapping.column].str.strip()
        if field.kind == MONEY:
            value = _validate_money(raw, field, mapping, issues)
        elif field.kind == DATE:
            value = _validate_date(raw, field, mapping, issues)
        elif field.kind == INT:
            value = _validate_int(raw, field, issues)
        elif field.kind == ENUM:
            value = _validate_enum(raw, field, mapping, issues)
        elif field.kind == REF:
            value = _validate_reference(raw, field, couriers, issues)
        elif field.kind == ID:
            value = _validate_identifier(raw, field, issues)
        else:
            value = _validate_length(raw, field, issues, raw)
        if field.required:
            empty_input = raw.str.casefold().isin(_NOT_AVAILABLE) | (raw == "")
            issues.add(
                empty_input,
                field.name,
                "missing_required",
                ERROR,
                f"{field.label} is required, but this row has no value.",
                raw,
            )
        canonical[field.name] = value

    for group in schema.any_of:
        mapped = [name for name in group if name in canonical]
        unlinked = pd.Series(True, index=frame.index)
        for name in mapped:
            unlinked &= canonical[name] == ""
        labels = " or ".join(schema.get(name).label for name in group)
        issues.add(
            unlinked,
            None,
            "no_order_link",
            WARNING,
            f"This row has no {labels}. It will be imported, but must be matched to an order "
            "by a person.",
        )

    if file_type == FileType.ORDER and "delivered_at" in canonical:
        issues.add(
            (canonical["delivery_status"] == DeliveryStatus.DELIVERED.value)
            & (canonical["delivered_at"] == ""),
            "delivered_at",
            "delivered_without_date",
            WARNING,
            "This order is delivered but has no delivery date, so its payment due date can't be "
            "worked out.",
        )

    data = pd.DataFrame(canonical, index=frame.index)
    data[ROW_COLUMN] = rows
    invalid = issues.error & ~summary
    importable = ~summary & ~invalid
    keys = _dedupe_key(data[importable].astype(str), schema, file_type)
    data["dedupe_key"] = ""
    data.loc[keys.index, "dedupe_key"] = keys
    repeats = keys[keys.duplicated(keep="first")].index
    duplicate = pd.Series(False, index=frame.index)
    duplicate.loc[repeats] = True
    issues.add(
        duplicate,
        None,
        "duplicate_in_file",
        DUPLICATE,
        "This row repeats an earlier row in the same file, so it was left out.",
    )

    all_issues = issues.frame()
    if not all_issues.empty:
        # Issues on rows left out for another reason would only add noise.
        hide = all_issues["row_number"].isin(rows[summary]) & (all_issues["code"] != "summary_row")
        all_issues = all_issues[~hide]

    keep = importable & ~duplicate
    warned_rows = all_issues.loc[all_issues["severity"] == WARNING, "row_number"]
    return Validated(
        stage=data[keep].reset_index(drop=True),
        issues=all_issues.sort_values("row_number", kind="stable").reset_index(drop=True),
        preview=_preview(frame, data, table, spec, all_issues, invalid | summary | duplicate),
        rows_total=int(len(frame)),
        rows_valid=int(keep.sum()),
        rows_invalid=int(invalid.sum()),
        rows_duplicate=int(duplicate.sum()),
        rows_excluded=int(summary.sum()),
        rows_warned=int(rows[keep].isin(warned_rows).sum()),
    )


def _preview(frame, data, table, spec, issues, problem) -> list[dict]:
    """The first rows, plus the first problem rows, so that the sample shows the problems."""
    head = list(frame.index[: PREVIEW_ROWS])
    extra = [i for i in frame.index[problem][: PREVIEW_PROBLEM_ROWS] if i not in head]
    by_row: dict[int, list[dict]] = {}
    selected_rows = frame.loc[head + extra, ROW_COLUMN]
    if not issues.empty:
        relevant = issues[issues["row_number"].isin(selected_rows)]
        for item in relevant.to_dict("records"):
            by_row.setdefault(int(item["row_number"]), []).append(
                {
                    "field": item["field"],
                    "code": item["code"],
                    "severity": item["severity"],
                    "message": item["message"],
                }
            )
    result = []
    for index in head + extra:
        number = int(frame.at[index, ROW_COLUMN])
        row_issues = by_row.get(number, [])
        severities = {issue["severity"] for issue in row_issues}
        status = (
            ERROR if ERROR in severities
            else DUPLICATE if DUPLICATE in severities
            else WARNING if WARNING in severities
            else "ok"
        )
        result.append(
            {
                "row_number": number,
                "status": status,
                "values": {
                    name: _plain(frame.at[index, mapping.column])
                    for name, mapping in spec.fields.items()
                },
                "canonical": {
                    name: _plain(data.at[index, name])
                    for name in spec.fields
                    if name in data.columns
                },
                "issues": row_issues,
            }
        )
    return result


def _plain(value) -> str:
    return "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)


def issue_groups(issues: pd.DataFrame) -> list[dict]:
    """Issues grouped by cause. Fixing one cause usually fixes thousands of rows."""
    if issues.empty:
        return []
    grouped = issues.groupby(["severity", "code", "field", "message"], dropna=False, sort=False)
    result = []
    for (severity, code, field, message), rows in grouped:
        examples = [value for value in rows["raw_value"].dropna().unique()[:3] if value != ""]
        result.append(
            {
                "severity": severity,
                "code": code,
                "field": None if pd.isna(field) else field,
                "message": message,
                "count": int(len(rows)),
                "first_rows": [int(number) for number in rows["row_number"].head(5)],
                "examples": [str(value) for value in examples],
            }
        )
    order = {ERROR: 0, DUPLICATE: 1, WARNING: 2}
    result.sort(key=lambda group: (order[group["severity"]], -group["count"]))
    return result[:MAX_ISSUE_GROUPS]


def courier_lookup(db: Session) -> dict[str, int]:
    rows = db.execute(select(Partner.name, Partner.id).where(Partner.type == PartnerType.COURIER))
    return {name.casefold(): partner_id for name, partner_id in rows}


# --- Duplicates already in the database ----------------------------------------------

_TABLES = {
    FileType.ORDER: "orders",
    FileType.SETTLEMENT: "settlement_lines",
    FileType.COD_REMITTANCE: "cod_remittances",
    FileType.FREIGHT_INVOICE: "freight_invoice_lines",
    FileType.RETURN_RECORD: "return_records",
    FileType.WAREHOUSE_RECEIPT: "warehouse_receipts",
}


def count_existing(db: Session, stage: pd.DataFrame, file_type: FileType, source: dict) -> int:
    if stage.empty:
        return 0
    keys = stage["dedupe_key"].tolist()
    if file_type == FileType.ORDER:
        statement = text(
            "SELECT count(*) FROM orders WHERE channel_id = :channel "
            "AND external_order_id = ANY(:keys)"
        )
        return db.scalar(statement, {"channel": source["channel_id"], "keys": keys})
    if file_type == FileType.WAREHOUSE_RECEIPT:
        statement = text("SELECT count(*) FROM warehouse_receipts WHERE receipt_ref = ANY(:keys)")
        return db.scalar(statement, {"keys": keys})
    if file_type == FileType.FREIGHT_INVOICE:
        statement = text(
            "SELECT count(*) FROM freight_invoice_lines WHERE partner_id = :partner "
            "AND invoice_no || chr(31) || tracking_id || chr(31) || shipment_type = ANY(:keys)"
        )
        return db.scalar(statement, {"partner": source["partner_id"], "keys": keys})
    statement = text(
        f"SELECT count(*) FROM {_TABLES[file_type]} "  # noqa: S608 - fixed table names
        "WHERE partner_id = :partner AND dedupe_key = ANY(:keys)"
    )
    return db.scalar(statement, {"partner": source["partner_id"], "keys": keys})


# --- Bulk load -----------------------------------------------------------------------

_CASTS = {MONEY: "numeric", DATE: "date", INT: "integer", REF: "integer"}


def _copy_csv(db: Session, statement: str, frame: pd.DataFrame) -> None:
    """COPY is the fastest load Postgres offers. Empty values arrive as NULL."""
    buffer = io.StringIO()
    frame.to_csv(buffer, header=False, index=False, lineterminator="\n")
    payload = buffer.getvalue().encode()
    connection = db.connection().connection.dbapi_connection
    with connection.cursor() as cursor, cursor.copy(statement) as copy:
        for start in range(0, len(payload), 1024 * 1024):
            copy.write(payload[start : start + 1024 * 1024])


@dataclass
class LoadResult:
    inserted: int
    updated: int
    duplicates: pd.DataFrame  # row numbers already in the database, or unchanged


def load_rows(
    db: Session,
    stage: pd.DataFrame,
    file_type: FileType,
    spec: MappingSpec,
    file_id: int,
    source: dict,
) -> LoadResult:
    schema = schema_for(file_type)
    fields = [f for f in schema.fields if f.name in stage.columns]
    names = [f.name for f in fields]
    if stage.empty:
        return LoadResult(0, 0, pd.DataFrame({"row_number": []}))

    columns = [ROW_COLUMN, "dedupe_key", *names]
    definition = ", ".join(f'"{name}" text' for name in columns)
    db.execute(text(f"CREATE TEMP TABLE import_stage ({definition}) ON COMMIT DROP"))
    quoted = ", ".join(f'"{name}"' for name in columns)
    _copy_csv(db, f"COPY import_stage ({quoted}) FROM STDIN (FORMAT csv)", stage[columns])

    def cast(field: Field) -> str:
        expression = f'"{field.name}"'
        return f"{expression}::{_CASTS[field.kind]}" if field.kind in _CASTS else expression

    targets = [f.column for f in fields]
    selects = [cast(f) for f in fields]
    params = {"file": file_id, **source}
    row_number = f'"{ROW_COLUMN}"::integer'

    if file_type == FileType.ORDER:
        assignments = [
            # A blank optional cell in a later file doesn't erase what's already known.
            f"{f.column} = EXCLUDED.{f.column}" if f.required
            else f"{f.column} = COALESCE(EXCLUDED.{f.column}, orders.{f.column})"
            for f in fields
            if f.name != "order_id"
        ]
        comparison_left = ", ".join(f"orders.{f.column}" for f in fields if f.name != "order_id")
        comparison_right = ", ".join(
            f"EXCLUDED.{f.column}" if f.required
            else f"COALESCE(EXCLUDED.{f.column}, orders.{f.column})"
            for f in fields
            if f.name != "order_id"
        )
        statement = f"""
            INSERT INTO orders (channel_id, source_file_id, source_row_number, {", ".join(targets)})
            SELECT :channel_id, :file, {row_number}, {", ".join(selects)} FROM import_stage
            ON CONFLICT (channel_id, external_order_id) DO UPDATE SET
                {", ".join(assignments)},
                source_file_id = EXCLUDED.source_file_id,
                source_row_number = EXCLUDED.source_row_number,
                updated_at = now()
            WHERE ({comparison_left}) IS DISTINCT FROM ({comparison_right})
            RETURNING source_row_number, (xmax = 0) AS inserted
        """  # noqa: S608 - names come from the fixed schema
        changed = db.execute(text(statement), params).all()
        inserted = sum(1 for row in changed if row.inserted)
        touched = {row.source_row_number for row in changed}
        unchanged = stage.loc[~stage[ROW_COLUMN].isin(touched), ROW_COLUMN]
        return LoadResult(
            inserted, len(changed) - inserted, pd.DataFrame({"row_number": unchanged.to_numpy()})
        )

    extra_target, extra_select = "", ""
    if file_type not in (FileType.FREIGHT_INVOICE, FileType.WAREHOUSE_RECEIPT):
        extra_target, extra_select = ", dedupe_key", ", dedupe_key"
    statement = f"""
        INSERT INTO {_TABLES[file_type]}
            (partner_id, source_file_id, source_row_number{extra_target}, {", ".join(targets)})
        SELECT :partner_id, :file, {row_number}{extra_select}, {", ".join(selects)}
        FROM import_stage
        ON CONFLICT DO NOTHING
        RETURNING source_row_number
    """  # noqa: S608 - names come from the fixed schema
    inserted = {row[0] for row in db.execute(text(statement), params)}
    repeats = stage.loc[~stage[ROW_COLUMN].isin(inserted), ROW_COLUMN]
    return LoadResult(len(inserted), 0, pd.DataFrame({"row_number": repeats.to_numpy()}))


def store_issues(db: Session, import_job_id: int, issues: pd.DataFrame) -> bool:
    """Keeps the worst issues first, up to a cap. Returns whether any were left out.

    Counts always stay exact. The cap protects Neon's free storage from a bad file.
    """
    if issues.empty:
        return False
    order = issues["severity"].map({ERROR: 0, DUPLICATE: 1, WARNING: 2})
    ranked = issues.assign(_rank=order).sort_values(["_rank", "row_number"], kind="stable")
    kept = ranked.head(MAX_STORED_ISSUES).drop(columns="_rank")
    kept.insert(0, "import_job_id", import_job_id)
    _copy_csv(
        db,
        "COPY import_row_errors "
        "(import_job_id, row_number, field, code, severity, message, raw_value) "
        "FROM STDIN (FORMAT csv)",
        kept[["import_job_id", *ISSUE_COLUMNS]],
    )
    return len(issues) > MAX_STORED_ISSUES


def source_of(db: Session, partner_id: int | None, channel_id: int | None) -> dict:
    if channel_id is not None:
        if db.get(SalesChannel, channel_id) is None:
            raise FileProblem("This file's sales channel no longer exists.")
        return {"channel_id": channel_id}
    return {"partner_id": partner_id}


