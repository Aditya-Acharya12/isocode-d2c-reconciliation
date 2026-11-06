"""Reads CSV and XLSX into strings. Loaded only by jobs, because pandas is heavy.

Everything stays text at this stage, so leading zeros and long AWBs survive, and
nothing is interpreted until a person has approved how to read it.
"""

import csv
import datetime as dt
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from python_calamine import CalamineWorkbook

from app.core.config import get_settings
from app.services.ingestion.canonical import DATE_FORMATS

EXTRA_COLUMN = "__extra__"
ROW_COLUMN = "__row__"
HEADER_SCAN_ROWS = 30
SAMPLE_ROWS = 20
PROFILE_SAMPLES = 5
PROFILE_DISTINCT = 25
MIN_DATE_FORMAT_FIT = 0.6
DATE_FORMAT_SAMPLE = 200
_DATE_SHAPE = r"\d{1,4}[-/. ](?:\d{1,2}|[A-Za-z]{3})[-/. ]\d{2,4}"
MAX_UNCOMPRESSED_XLSX = 400 * 1024 * 1024
DELIMITERS = (",", ";", "\t", "|")
_NUMERIC_CELL = re.compile(r"^[\s₹(\-]*[\d,]+(?:\.\d+)?\s*\)?$")


class FileProblem(Exception):
    """The file itself can't be used. The message is shown to the person who uploaded it."""


@dataclass
class Table:
    frame: pd.DataFrame  # all text, with ROW_COLUMN holding each row's number in the file
    columns: list[str]
    header_row: int
    sheet: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    sheets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    malformed_rows: bool = False


def _clean_headers(raw: list[str]) -> list[str]:
    names, seen = [], Counter()
    for index, value in enumerate(raw):
        name = str(value).strip() or f"Column {index + 1}"
        seen[name] += 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return names


def _find_header(rows: list[list[str]]) -> int:
    """The first row that looks like labels. Reports often have title rows above it."""
    widths = [sum(bool(str(cell).strip()) for cell in row) for row in rows]
    usable = [width for width in widths if width >= 2]
    if not usable:
        raise FileProblem("The file needs at least two columns, and none were found.")
    typical = Counter(usable).most_common(1)[0][0]
    for index, row in enumerate(rows):
        cells = [str(cell).strip() for cell in row if str(cell).strip()]
        if len(cells) < max(2, typical * 0.6):
            continue
        numeric = sum(bool(_NUMERIC_CELL.match(cell)) for cell in cells)
        if numeric == 0 and len(set(cells)) >= len(cells) * 0.8:
            return index
    return 0


def _detect_csv_encoding(content: bytes) -> str:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    try:
        content.decode("utf-8-sig")
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"


def _detect_delimiter(lines: list[str]) -> str:
    best, best_score = ",", -1.0
    for delimiter in DELIMITERS:
        counts = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        wide = [count for count in counts if count >= 2]
        if not wide:
            continue
        typical, frequency = Counter(wide).most_common(1)[0]
        score = frequency + typical / 100
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _read_csv(content: bytes, header_row: int | None, usecols: list[str] | None) -> Table:
    if b"\x00" in content[:4096] and content[:2] not in (b"\xff\xfe", b"\xfe\xff"):
        raise FileProblem("This doesn't look like a text CSV file.")
    if not content.strip():
        raise FileProblem("The file is empty.")
    encoding = _detect_csv_encoding(content)
    text_head = content[:256 * 1024].decode(encoding, errors="ignore")
    lines = text_head.splitlines()[:HEADER_SCAN_ROWS]
    delimiter = _detect_delimiter(lines)
    head_rows = list(csv.reader(lines, delimiter=delimiter))
    if header_row is None:
        header_row = _find_header(head_rows)
    if header_row >= len(head_rows):
        raise FileProblem(f"The file has no row {header_row + 1} to use as a header.")
    columns = _clean_headers(head_rows[header_row])
    wanted = columns if usecols is None else [c for c in columns if c in usecols]
    # The first columns are always read, so that summary rows can be recognised.
    wanted = list(dict.fromkeys([*columns[:3], *wanted]))
    malformed = False
    try:
        frame = pd.read_csv(
            io.BytesIO(content),
            sep=delimiter,
            header=None,
            names=[*columns, EXTRA_COLUMN],
            usecols=[*wanted, EXTRA_COLUMN],
            skiprows=header_row + 1,
            dtype=str,
            na_filter=False,
            encoding=encoding,
            encoding_errors="replace",
            engine="c",
            index_col=False,
            skip_blank_lines=False,
        )
        frame = frame.fillna("")
    except pd.errors.ParserError:
        # Some rows have too many fields. Read them slowly, so they can be reported.
        malformed = True
        frame = _read_csv_tolerant(content, encoding, delimiter, header_row, columns, wanted)
    frame.insert(0, ROW_COLUMN, np.arange(len(frame)) + header_row + 2)
    table = Table(frame, columns, header_row, encoding=encoding, delimiter=delimiter)
    table.malformed_rows = malformed
    return table


def _read_csv_tolerant(content, encoding, delimiter, header_row, columns, wanted) -> pd.DataFrame:
    stream = io.StringIO(content.decode(encoding, errors="replace"), newline="")
    data: dict[str, list[str]] = {name: [] for name in wanted}
    data[EXTRA_COLUMN] = []
    positions = [(name, columns.index(name)) for name in wanted]
    width = len(columns)
    for index, row in enumerate(csv.reader(stream, delimiter=delimiter)):
        if index <= header_row:
            continue
        for name, position in positions:
            data[name].append(row[position] if position < len(row) else "")
        data[EXTRA_COLUMN].append(",".join(row[width:]) if len(row) > width else "")
    return pd.DataFrame(data, dtype=str)


def _cell_to_text(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        # Excel stores all numbers as floats, which must not reach identifiers as "123.0".
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _check_xlsx_container(content: bytes) -> None:
    if not content.startswith(b"PK"):
        raise FileProblem("This doesn't look like an Excel .xlsx file.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            if "xl/workbook.xml" not in names:
                raise FileProblem("This doesn't look like an Excel .xlsx file.")
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_XLSX:
                raise FileProblem("This workbook is too large when uncompressed.")
    except zipfile.BadZipFile as exc:
        raise FileProblem("This Excel file is damaged and can't be opened.") from exc


def _read_xlsx(
    content: bytes, sheet: str | None, header_row: int | None, usecols: list[str] | None
) -> Table:
    _check_xlsx_container(content)
    try:
        workbook = CalamineWorkbook.from_filelike(io.BytesIO(content))
    except Exception as exc:  # calamine raises several private types for damaged files
        raise FileProblem("This Excel file is damaged and can't be opened.") from exc
    sheets = list(workbook.sheet_names)
    rows: list[list] = []
    chosen = None
    candidates = [sheet] if sheet else sheets
    for name in candidates:
        if name not in sheets:
            raise FileProblem(f"This workbook has no sheet called '{name}'.")
        rows = workbook.get_sheet_by_name(name).to_python(skip_empty_area=False)
        if any(any(cell not in (None, "") for cell in row) for row in rows[:HEADER_SCAN_ROWS]):
            chosen = name
            break
    if chosen is None:
        raise FileProblem("The workbook has no data.")
    width = max(len(row) for row in rows[:HEADER_SCAN_ROWS])
    head_rows = [[_cell_to_text(cell) for cell in row] for row in rows[:HEADER_SCAN_ROWS]]
    if header_row is None:
        header_row = _find_header(head_rows)
    if header_row >= len(rows):
        raise FileProblem(f"The sheet has no row {header_row + 1} to use as a header.")
    header_cells = list(head_rows[header_row]) + [""] * width
    while len(header_cells) > 2 and not header_cells[-1].strip():
        header_cells.pop()
    columns = _clean_headers(header_cells)
    wanted = columns if usecols is None else [c for c in columns if c in usecols]
    wanted = list(dict.fromkeys([*columns[:3], *wanted]))
    body = rows[header_row + 1 :]
    data: dict[str, list[str]] = {}
    for name in wanted:
        position = columns.index(name)
        data[name] = [
            _cell_to_text(row[position]) if position < len(row) else "" for row in body
        ]
    extra_positions = len(columns)
    data[EXTRA_COLUMN] = [
        ",".join(_cell_to_text(cell) for cell in row[extra_positions:] if cell not in (None, ""))
        if len(row) > extra_positions else ""
        for row in body
    ]
    frame = pd.DataFrame(data, dtype=str)
    frame.insert(0, ROW_COLUMN, np.arange(len(frame)) + header_row + 2)
    return Table(frame, columns, header_row, sheet=chosen, sheets=sheets)


def read_table(
    content: bytes,
    filename: str,
    *,
    sheet: str | None = None,
    header_row: int | None = None,
    usecols: list[str] | None = None,
) -> Table:
    """Reads a file, and drops rows that are completely blank."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "csv":
        table = _read_csv(content, header_row, usecols)
    elif suffix == "xlsx":
        table = _read_xlsx(content, sheet, header_row, usecols)
    else:
        raise FileProblem("Only .csv and .xlsx files are supported.")

    value_columns = [c for c in table.frame.columns if c != ROW_COLUMN]
    has_content = (table.frame[value_columns].apply(lambda s: s.str.strip() != "")).any(axis=1)
    table.frame = table.frame[has_content].reset_index(drop=True)
    if len(table.frame) > get_settings().max_import_rows:
        raise FileProblem(
            f"This file has {len(table.frame):,} rows. The limit is "
            f"{get_settings().max_import_rows:,} per file, so please split it."
        )
    return table


def detect_date_format(counts: pd.Series) -> tuple[str | None, list[str]]:
    """The likely format, and every format that fits. More than one means it's ambiguous.

    Takes each distinct value with how often it occurs, so that the score reflects rows.
    """
    if counts.empty:
        return None, []
    series = pd.Series(counts.index, dtype=str)
    looks_like_dates = series.str.contains(_DATE_SHAPE, regex=True)
    # Trying every format on every column is wasted effort on a small instance.
    if looks_like_dates.mean() < MIN_DATE_FORMAT_FIT:
        return None, []
    weights = counts.to_numpy()
    total = weights.sum()
    scores: dict[str, float] = {}
    for label, pattern in DATE_FORMATS.items():
        parsed = pd.to_datetime(series, format=pattern, errors="coerce")
        score = float(weights[parsed.notna().to_numpy()].sum() / total)
        # A few bad values shouldn't hide the right format. Validation will report them.
        if score >= MIN_DATE_FORMAT_FIT:
            scores[label] = score
    if not scores:
        return None, []
    best = max(scores.values())
    fits = [label for label, score in scores.items() if score == best]
    # Indian reports use day-first, so that's the suggestion when both fit.
    preferred = next((label for label in fits if label.startswith("DD")), fits[0])
    return preferred, fits


def analyse(content: bytes, filename: str) -> dict:
    """The structure of a file, as shown on the mapping screen."""
    table = read_table(content, filename)
    frame = table.frame
    if frame.empty:
        raise FileProblem("The file has a header row, but no data rows.")
    if len(table.columns) < 2:
        raise FileProblem("The file needs at least two columns, and only one was found.")
    profiles: dict[str, dict] = {}
    for column in table.columns:
        series = frame[column].str.strip()
        filled = series[series != ""]
        counts = filled.value_counts()
        distinct = counts.index[:PROFILE_DISTINCT].tolist()
        samples = filled.drop_duplicates().head(PROFILE_SAMPLES).tolist()
        date_format, date_formats = detect_date_format(counts.head(DATE_FORMAT_SAMPLE))
        profiles[column] = {
            "samples": samples,
            "distinct": distinct,
            "distinct_count": int(counts.size),
            "empty_count": int((series == "").sum()),
            "date_format": date_format,
            "date_formats": date_formats,
        }
    return {
        "kind": filename.rsplit(".", 1)[-1].lower(),
        "encoding": table.encoding,
        "delimiter": table.delimiter,
        "sheet": table.sheet,
        "sheets": table.sheets,
        "header_row": table.header_row,
        "columns": table.columns,
        "row_count": int(len(frame)),
        "sample_rows": frame[[ROW_COLUMN, *table.columns]].head(SAMPLE_ROWS).to_dict("records"),
        "profiles": profiles,
        "malformed_rows": table.malformed_rows,
    }
