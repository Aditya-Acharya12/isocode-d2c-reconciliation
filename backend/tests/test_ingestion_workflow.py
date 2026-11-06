"""The whole workflow, through the real API and the real job code, for every file type."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    BackgroundJob,
    CodRemittance,
    ColumnMapping,
    FreightInvoiceLine,
    ImportJob,
    MappingVersion,
    Order,
    ReturnRecord,
    SettlementLine,
    UploadedFile,
    UploadedFileBlob,
    WarehouseReceipt,
)
from app.services.jobs import run_until_idle
from tests.ingestion_helpers import (
    FILES,
    draft_for,
    imported,
    sha256,
    spec_from_draft,
    start_import,
    upload,
    uploaded,
    validate,
    workbook,
)

ORDERS = (
    b"Order No,Order Date,Order Value,Payment Mode,Status,AWB No,Courier,Delivered On,Qty\n"
    b"ORD-W-204118,01/10/2026,1499.00,COD,Delivered,AWB1001,Courier Aggregator A,05/10/2026,1\n"
    b"ORD-W-204377,02/10/2026,2398.00,Prepaid,RTO,AWB1002,Courier Aggregator A,,2\n"
)
SETTLEMENTS = (
    b"Settlement ID,Sub Order ID,Net Paid,Settlement Date,TCS,TDS,Commission\n"
    b"SET-1,ORD-M-88120,855.09,12/10/2026,5.71,1.20,299.75\n"
    b"SET-2,ORD-M-88121,0.00,12/10/2026,,,\n"
)
COD = (
    b"AWB_No,COD_Collected,Net_Rem,Remittance Date\n"
    b"AWB1001,1499.00,1409.00,15/10/2026\n"
    b"AWB1003,799.00,751.00,15/10/2026\n"
)
FREIGHT = (
    b"Invoice No,AWB No,Total Charge,Invoice Date,Shipment Type,Wt_Slab\n"
    b"INV-1,AWB1002,142.00,20/10/2026,RTO,0.5kg\n"
    b"INV-1,AWB1002,96.00,20/10/2026,Forward,0.5kg\n"
)
RETURNS = (
    b"AWB No,Return Type,Return Date,Quantity\n"
    b"AWB1002,RTO,18/10/2026,2\n"
)
RECEIPTS = (
    b"GRN No,AWB No,Received On,Qty Received,Condition\n"
    b"GRN-1,AWB1002,19/10/2026,2,Good\n"
)

CASES = [
    ("order", ORDERS, "channel", Order, 2),
    ("settlement", SETTLEMENTS, "marketplace", SettlementLine, 2),
    ("cod_remittance", COD, "courier", CodRemittance, 2),
    ("freight_invoice", FREIGHT, "courier", FreightInvoiceLine, 2),
    ("return_record", RETURNS, "courier", ReturnRecord, 1),
    ("warehouse_receipt", RECEIPTS, "warehouse", WarehouseReceipt, 1),
]


def _source(file_type: str, sources: dict, key: str) -> dict:
    return {"channel_id": sources[key]} if key == "channel" else {"partner_id": sources[key]}


@pytest.mark.parametrize(("file_type", "content", "source_key", "model", "count"), CASES)
@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_every_file_type_imports_end_to_end(
    finance_client, db, sources, file_type, content, source_key, model, count, kind
):
    if kind == "xlsx":
        rows = [line.split(",") for line in content.decode().splitlines()]
        content = workbook(rows)
    result = imported(
        finance_client, content, f"partner-file.{kind}", file_type,
        **_source(file_type, sources, source_key),
    )
    assert result["status"] == "succeeded", result
    assert result["rows_total"] == count
    assert result["rows_inserted"] == count
    assert result["rows_rejected"] == 0
    assert db.scalar(select(func.count()).select_from(model)) == count
    stored = db.scalars(select(model).order_by(model.source_row_number)).first()
    assert stored.source_file_id == result["file"]["id"]
    assert stored.source_row_number == 2


def test_imported_values_are_exact(finance_client, db, sources):
    imported(finance_client, ORDERS, "orders.csv", "order", channel_id=sources["channel"])
    imported(
        finance_client, SETTLEMENTS, "settlement.csv", "settlement",
        partner_id=sources["marketplace"],
    )
    order = db.scalar(select(Order).where(Order.external_order_id == "ORD-W-204118"))
    assert order.order_value == Decimal("1499.00")
    assert order.order_date == date(2026, 10, 1)  # read as day-first, as the mapping says
    assert order.delivered_at == date(2026, 10, 5)
    assert order.payment_method == "cod"
    assert order.delivery_status == "delivered"
    assert order.tracking_id == "AWB1001"
    assert order.courier_id == sources["courier"]
    assert order.channel_id == sources["channel"]

    rto = db.scalar(select(Order).where(Order.external_order_id == "ORD-W-204377"))
    assert rto.delivery_status == "rto"
    assert rto.delivered_at is None

    line = db.scalar(select(SettlementLine).where(SettlementLine.settlement_ref == "SET-1"))
    assert line.net_amount == Decimal("855.09")
    assert line.tcs_amount == Decimal("5.71")
    assert line.tds_amount == Decimal("1.20")
    assert line.external_order_ref == "ORD-M-88120"
    # "Not reported" must stay different from a reported zero: tax logic depends on it.
    unreported = db.scalar(select(SettlementLine).where(SettlementLine.settlement_ref == "SET-2"))
    assert unreported.tcs_amount is None
    assert unreported.net_amount == Decimal("0.00")


def test_the_original_file_is_kept_and_can_be_downloaded(finance_client, db, sources):
    file = uploaded(finance_client, ORDERS, "orders.csv", "order", channel_id=sources["channel"])
    assert file["sha256"] == sha256(ORDERS)
    assert file["is_synthetic"] is True
    blob = db.get(UploadedFileBlob, file["id"])
    assert len(blob.content_gzip) > 0
    response = finance_client.get(f"{FILES}/{file['id']}/download")
    assert response.content == ORDERS
    assert "attachment" in response.headers["content-disposition"]


# --- Upload checks ---------------------------------------------------------------------


def test_the_same_file_cannot_be_uploaded_twice(finance_client, db, sources):
    first = upload(finance_client, COD, "cod.csv", "cod_remittance", partner_id=sources["courier"])
    assert first.status_code == 201
    second = upload(
        finance_client, COD, "renamed.csv", "cod_remittance", partner_id=sources["courier"]
    )
    assert second.status_code == 409
    assert second.json()["code"] == "duplicate_file"
    assert second.json()["details"]["file_id"] == first.json()["id"]
    assert db.scalar(select(func.count()).select_from(UploadedFile)) == 1


def test_duplicates_are_refused_across_sources_too(finance_client, sources, db):
    from app.models import Partner

    other = Partner(name="B", type="courier", settlement_terms_days=1, return_grace_days=1)
    db.add(other)
    db.commit()
    upload(finance_client, COD, "cod.csv", "cod_remittance", partner_id=sources["courier"])
    response = upload(finance_client, COD, "cod.csv", "cod_remittance", partner_id=other.id)
    assert response.status_code == 409


def test_a_corrupted_upload_is_refused(finance_client, sources):
    response = finance_client.post(
        FILES,
        files={"file": ("cod.csv", COD)},
        data={
            "file_type": "cod_remittance",
            "partner_id": str(sources["courier"]),
            "sha256": sha256(b"something else"),
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "upload_corrupted"


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("report.pdf", b"%PDF-1.7 nonsense", "unsupported_format"),
        ("report.xls", b"\xd0\xcf\x11\xe0 old excel", "unsupported_format"),
        ("noextension", b"a,b\n1,2\n", "unsupported_format"),
        ("fake.xlsx", b"a,b\n1,2\n", "unsupported_format"),
        ("fake.csv", b"PK\x03\x04 zip", "unsupported_format"),
        ("empty.csv", b"", "empty_file"),
    ],
)
def test_invalid_formats_are_refused(finance_client, db, sources, name, content, code):
    response = upload(finance_client, content, name, "order", channel_id=sources["channel"])
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert db.scalar(select(func.count()).select_from(UploadedFile)) == 0


def test_files_over_the_size_limit_are_refused(finance_client, sources):
    big = b"a,b\n" + b"1,2\n" * (1024 * 1024)  # MAX_UPLOAD_MB is 1 in tests
    response = upload(finance_client, big, "big.csv", "order", channel_id=sources["channel"])
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("file_type", "source_key", "field", "ok"),
    [
        ("order", "courier", "partner_id", False),  # orders come from a channel
        ("settlement", "channel", "channel_id", False),
        ("cod_remittance", "marketplace", "partner_id", False),  # COD comes from a courier
        ("warehouse_receipt", "courier", "partner_id", False),
        ("cod_remittance", "courier", "partner_id", True),
    ],
)
def test_files_must_match_their_source(
    finance_client, sources, file_type, source_key, field, ok
):
    response = upload(finance_client, b"a,b\n1,2\n", "a.csv", file_type, **{field: sources[source_key]})
    assert (response.status_code == 201) is ok, response.text


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("headers.csv", b"Order No,Order Date\n", "no data rows"),
        ("single.csv", b"just one column\nvalue\n", "two columns"),
        ("binary.csv", b"\x00\x01\x02binary\x00\x00" * 50, "text CSV"),
        ("damaged.xlsx", b"PK\x03\x04 not really a workbook", "damaged"),
    ],
)
def test_unusable_files_fail_analysis_with_a_clear_message(
    finance_client, sources, name, content, message
):
    file = uploaded(finance_client, content, name, "order", channel_id=sources["channel"])
    assert file["stage"] == "analysis_failed"
    assert message in file["error"]
    assert "Traceback" not in file["error"]


def test_failed_analysis_can_be_deleted_and_uploaded_again(finance_client, db, sources):
    content = b"Order No,Order Date\n"
    file = uploaded(finance_client, content, "a.csv", "order", channel_id=sources["channel"])
    assert finance_client.delete(f"{FILES}/{file['id']}").status_code == 204
    assert db.get(UploadedFileBlob, file["id"]) is None
    assert upload(finance_client, content, "a.csv", "order", channel_id=sources["channel"]).status_code == 201


def test_an_imported_file_cannot_be_deleted(finance_client, sources):
    result = imported(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    response = finance_client.delete(f"{FILES}/{result['file']['id']}")
    assert response.status_code == 409


# --- Structure -------------------------------------------------------------------------


def test_title_rows_above_the_header_are_skipped(finance_client, db, sources):
    content = b"Partner Report\nGenerated 01/11/2026,,,\n,,,\n" + COD
    result = imported(finance_client, content, "cod.csv", "cod_remittance", partner_id=sources["courier"])
    assert result["rows_inserted"] == 2
    # Row numbers still refer to the real file, so people can find the row.
    numbers = db.scalars(select(CodRemittance.source_row_number).order_by(CodRemittance.id)).all()
    assert numbers == [5, 6]


def test_semicolons_and_excel_byte_order_marks_are_handled(finance_client, sources):
    content = b"\xef\xbb\xbf" + COD.replace(b",", b";")
    file = uploaded(finance_client, content, "cod.csv", "cod_remittance", partner_id=sources["courier"])
    assert file["detection"]["delimiter"] == ";"
    assert file["detection"]["columns"][0] == "AWB_No"


def test_windows_1252_files_are_readable(finance_client, sources):
    content = "Order No,Order Date,Order Value,Payment Mode,Status\nORD-Ü,01/10/2026,10,COD,Delivered\n"
    file = uploaded(
        finance_client, content.encode("cp1252"), "o.csv", "order", channel_id=sources["channel"]
    )
    assert file["detection"]["sample_rows"][0]["Order No"] == "ORD-Ü"


def test_excel_values_keep_their_meaning(finance_client, db, sources):
    from datetime import datetime

    rows = [
        ["Monthly report", None, None, None, None],
        ["Order No", "Order Date", "Order Value", "Payment Mode", "Status", "AWB No"],
        ["ORD-1", datetime(2026, 10, 1), 1499, "COD", "Delivered", 123456789012],
        ["ORD-2", datetime(2026, 10, 2), 899.5, "Prepaid", "Delivered", "000123"],
    ]
    result = imported(
        finance_client, workbook(rows), "o.xlsx", "order", channel_id=sources["channel"]
    )
    assert result["rows_inserted"] == 2, result
    first = db.scalar(select(Order).where(Order.external_order_id == "ORD-1"))
    assert first.order_date == date(2026, 10, 1)
    assert first.order_value == Decimal("1499.00")
    assert first.tracking_id == "123456789012"  # a number in Excel, but not "123456789012.0"
    second = db.scalar(select(Order).where(Order.external_order_id == "ORD-2"))
    assert second.order_value == Decimal("899.50")
    assert second.tracking_id == "000123"  # leading zeros survive


def test_the_first_sheet_with_data_is_used(finance_client, sources):
    rows = [line.split(",") for line in COD.decode().splitlines()]
    file = uploaded(
        finance_client,
        workbook(rows, sheet="Data", extra_sheet_first=True),
        "cod.xlsx",
        "cod_remittance",
        partner_id=sources["courier"],
    )
    assert file["detection"]["sheet"] == "Data"
    assert file["detection"]["sheets"] == ["Empty", "Data"]


# --- Row validation --------------------------------------------------------------------

MESSY_ORDERS = (
    b"Order No,Order Date,Order Value,Payment Mode,Status,AWB No\n"
    b"ORD-1,01/10/2026,\xe2\x82\xb91,499.00,COD,Delivered,A1\n"  # unquoted comma: extra field
    b'ORD-2,01/10/2026,"\xe2\x82\xb91,499.50",COD,Delivered,A2\n'
    b"ORD-3,31/02/2026,100,COD,Delivered,A3\n"  # no such date
    b"ORD-4,01/10/2026,abc,COD,Delivered,A4\n"
    b"ORD-5,01/10/2026,-5,COD,Delivered,A5\n"
    b"ORD-6,01/10/2026,100,Barter,Delivered,A6\n"  # unknown payment method
    b",01/10/2026,100,COD,Delivered,A7\n"  # no order ID
    b"ORD-8,01/10/2026,100,COD,Delivered,1.23457E+11\n"  # Excel destroyed the AWB
    b"ORD-9,01/10/2026,100,COD,Delivered,A9\n"
    b"ORD-9,01/10/2026,100,COD,Delivered,A9\n"  # repeated row
    b"ORD-11,01/10/2026,10.555,COD,Delivered,A11\n"  # too many decimals: rounded, with a warning
    b"TOTAL,,5000,,,\n"
)


def _messy(client, sources):
    file = uploaded(client, MESSY_ORDERS, "m.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(client, file["id"]))
    return file, spec, validate(client, file["id"], spec)


def test_every_row_is_accounted_for_and_explained(finance_client, sources):
    _, _, job = _messy(finance_client, sources)
    result = job["result"]
    assert result["rows_total"] == 12
    assert result["rows_valid"] == 3  # ORD-2, ORD-9, ORD-11
    assert result["rows_invalid"] == 7
    assert result["rows_duplicate"] == 1
    assert result["rows_excluded"] == 1  # the totals row
    assert result["rows_warned"] == 1  # the rounded amount
    assert (
        result["rows_valid"] + result["rows_invalid"]
        + result["rows_duplicate"] + result["rows_excluded"]
    ) == result["rows_total"]

    groups = {group["code"]: group for group in result["issue_groups"]}
    assert {"too_many_columns", "invalid_date", "invalid_amount", "negative_amount",
            "unmapped_value", "missing_required", "corrupted_identifier", "duplicate_in_file",
            "amount_rounded", "summary_row"} <= set(groups)
    assert groups["invalid_date"]["first_rows"] == [4]  # the actual row in the file
    assert groups["invalid_date"]["examples"] == ["31/02/2026"]
    assert groups["invalid_date"]["severity"] == "error"
    assert groups["amount_rounded"]["severity"] == "warning"
    assert groups["duplicate_in_file"]["severity"] == "duplicate"
    assert "format" in groups["invalid_date"]["message"]
    assert "Excel" in groups["corrupted_identifier"]["message"]


def test_invalid_rows_must_be_acknowledged_before_importing(finance_client, db, sources):
    file, spec, job = _messy(finance_client, sources)
    refused = start_import(finance_client, file["id"], spec, job, acknowledged=0)
    assert refused.status_code == 409
    assert refused.json()["code"] == "acknowledgement_required"
    assert db.scalar(select(func.count()).select_from(ImportJob)) == 0

    assert start_import(finance_client, file["id"], spec, job).status_code == 202
    run_until_idle()
    orders = {order.external_order_id: order for order in db.scalars(select(Order))}
    assert set(orders) == {"ORD-2", "ORD-9", "ORD-11"}
    assert orders["ORD-2"].order_value == Decimal("1499.50")
    assert orders["ORD-11"].order_value == Decimal("10.56")  # rounded by Postgres, never a float


def test_failed_rows_can_be_inspected_and_downloaded(finance_client, sources):
    file, spec, job = _messy(finance_client, sources)
    import_id = start_import(finance_client, file["id"], spec, job).json()["import_id"]
    run_until_idle()

    detail = finance_client.get(f"/api/v1/imports/{import_id}").json()
    assert detail["rows_rejected"] == 7
    assert detail["rows_inserted"] == 3
    assert detail["summary"]["issues_truncated"] is False

    errors = finance_client.get(f"/api/v1/imports/{import_id}/errors?severity=error").json()
    assert errors["total"] >= 7  # a row can have more than one issue
    assert all(item["severity"] == "error" for item in errors["items"])
    bad_date = next(item for item in errors["items"] if item["code"] == "invalid_date")
    assert bad_date["row_number"] == 4
    assert bad_date["raw_value"] == "31/02/2026"

    every = finance_client.get(f"/api/v1/imports/{import_id}/errors").json()
    assert {item["severity"] for item in every["items"]} == {"error", "duplicate", "warning"}
    assert every["items"][0]["severity"] == "error"  # the worst come first

    download = finance_client.get(f"/api/v1/imports/{import_id}/errors.csv")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert "31/02/2026" in download.text


def test_row_issue_downloads_cannot_carry_spreadsheet_formulas(finance_client, sources):
    content = (
        b"Order No,Order Date,Order Value,Payment Mode,Status\n"
        b'ORD-1,01/10/2026,"=HYPERLINK(""http://evil"")",COD,Delivered\n'
        b"ORD-2,01/10/2026,10,COD,Delivered\n"
    )
    file = uploaded(finance_client, content, "f.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    import_id = start_import(finance_client, file["id"], spec, job).json()["import_id"]
    run_until_idle()
    text = finance_client.get(f"/api/v1/imports/{import_id}/errors.csv").text
    assert "'=HYPERLINK" in text
    assert ",=HYPERLINK" not in text


def test_validation_preview_shows_rows_and_their_problems(finance_client, sources):
    _, _, job = _messy(finance_client, sources)
    preview = job["result"]["preview"]
    assert len(preview) >= 12  # the first rows, and every problem row within the sample
    by_row = {row["row_number"]: row for row in preview}
    assert by_row[3]["status"] == "ok"
    assert by_row[3]["canonical"]["order_value"] == "1499.50"
    assert by_row[3]["canonical"]["order_date"] == "2026-10-01"
    assert by_row[3]["values"]["order_value"] == "₹1,499.50"  # as it appears in the file
    assert by_row[4]["status"] == "error"
    assert by_row[4]["issues"][0]["code"] == "invalid_date"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1499", "1499.00"),
        ("1,499.50", "1499.50"),
        ("₹ 1,499.50", "1499.50"),
        ("Rs. 899", "899.00"),
        ("INR 899.5", "899.50"),
        ("(142.00)", "-142.00"),
        ("142.00-", "-142.00"),
        ("-96", "-96.00"),
        ("+10", "10.00"),
        (".50", "0.50"),
    ],
)
def test_money_formats(finance_client, db, sources, raw, expected):
    content = f'Settlement ID,Sub Order ID,Net Paid,Settlement Date\nS1,O1,"{raw}",01/10/2026\n'
    imported(
        finance_client, content.encode(), "s.csv", "settlement", partner_id=sources["marketplace"]
    )
    assert str(db.scalar(select(SettlementLine.net_amount))) == expected


def test_a_money_columns_sign_can_be_reversed(finance_client, db, sources):
    file = uploaded(finance_client, SETTLEMENTS, "s.csv", "settlement", partner_id=sources["marketplace"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    spec["fields"]["net_amount"]["negate"] = True
    job = validate(finance_client, file["id"], spec)
    start_import(finance_client, file["id"], spec, job)
    run_until_idle()
    line = db.scalar(select(SettlementLine).where(SettlementLine.settlement_ref == "SET-1"))
    assert line.net_amount == Decimal("-855.09")


def test_blank_order_links_are_kept_with_a_warning(finance_client, db, sources):
    """The brief expects missing order IDs. They go to a person later, and aren't dropped."""
    content = (
        b"Settlement ID,Sub Order ID,Net Paid,Settlement Date\n"
        b"S1,,100,01/10/2026\n"
    )
    file = uploaded(finance_client, content, "s.csv", "settlement", partner_id=sources["marketplace"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    assert job["result"]["rows_valid"] == 1
    assert job["result"]["rows_warned"] == 1
    assert job["result"]["issue_groups"][0]["code"] == "no_order_link"
    start_import(finance_client, file["id"], spec, job)
    run_until_idle()
    assert db.scalar(select(func.count()).select_from(SettlementLine)) == 1


def test_an_unknown_courier_is_a_warning(finance_client, db, sources):
    content = (
        b"Order No,Order Date,Order Value,Payment Mode,Status,Courier\n"
        b"O1,01/10/2026,10,COD,Delivered,Nobody Express\n"
    )
    result = imported(finance_client, content, "o.csv", "order", channel_id=sources["channel"])
    assert result["rows_inserted"] == 1
    assert result["rows_warned"] == 1
    assert db.scalar(select(Order.courier_id)) is None


def test_a_wrong_date_format_shows_up_as_row_errors(finance_client, sources):
    file = uploaded(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    spec["fields"]["order_date"]["date_format"] = "YYYY-MM-DD"
    job = validate(finance_client, file["id"], spec)
    assert job["result"]["rows_invalid"] == 2
    assert job["result"]["rows_valid"] == 0
    refused = start_import(finance_client, file["id"], spec, job)
    assert refused.status_code == 422
    assert refused.json()["code"] == "nothing_to_import"


def test_rows_with_too_many_values_are_reported_not_silently_cut_short(finance_client, sources):
    content = (
        b"AWB_No,COD_Collected,Net_Rem,Remittance Date\n"
        b"A1,10,9,01/10/2026\n"
        b"A2,10,9,01/10/2026,extra,extra,extra,extra\n"
        b"A3,10,9,01/10/2026\n"
    )
    file = uploaded(finance_client, content, "c.csv", "cod_remittance", partner_id=sources["courier"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    assert job["result"]["rows_valid"] == 2
    assert job["result"]["rows_invalid"] == 1
    assert job["result"]["issue_groups"][0]["code"] == "too_many_columns"
    assert job["result"]["issue_groups"][0]["first_rows"] == [3]


def test_a_stray_quote_cannot_crash_the_parser(finance_client, sources):
    """An unclosed quote breaks the fast parser, which falls back to a tolerant one."""
    content = (
        b"AWB_No,COD_Collected,Net_Rem,Remittance Date\n"
        b"A1,10,9,01/10/2026\n"
        b'"A2,10,9,01/10/2026\n'
    )
    file = uploaded(finance_client, content, "c.csv", "cod_remittance", partner_id=sources["courier"])
    assert file["stage"] == "needs_mapping"
    assert file["detection"]["malformed_rows"] is True
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    assert job["status"] == "succeeded"
    assert job["result"]["rows_total"] == 2
    assert job["result"]["rows_valid"] == 1
    assert job["result"]["rows_invalid"] == 1


def test_a_format_is_still_suggested_when_a_few_dates_are_bad(finance_client, sources):
    rows = "".join(f"A{i},10,9,0{(i % 9) + 1}/10/2026\n" for i in range(9))
    content = ("AWB_No,COD_Collected,Net_Rem,Remittance Date\n" + rows + "A99,10,9,garbage\n").encode()
    file = uploaded(finance_client, content, "c.csv", "cod_remittance", partner_id=sources["courier"])
    profile = file["detection"]["profiles"]["Remittance Date"]
    assert profile["date_format"] == "DD/MM/YYYY"
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    assert job["result"]["rows_invalid"] == 1  # and the bad one is still reported


def test_day_and_month_ambiguity_is_exposed_for_a_person_to_decide(finance_client, sources):
    content = b"AWB_No,COD_Collected,Net_Rem,Remittance Date\nA1,10,9,01/02/2026\n"
    file = uploaded(finance_client, content, "c.csv", "cod_remittance", partner_id=sources["courier"])
    profile = file["detection"]["profiles"]["Remittance Date"]
    assert profile["date_format"] == "DD/MM/YYYY"  # the sensible default in India
    assert set(profile["date_formats"]) >= {"DD/MM/YYYY", "MM/DD/YYYY"}  # shown as a choice


def test_a_status_seen_for_the_first_time_gets_a_suggestion_on_a_saved_mapping(
    finance_client, db, sources
):
    imported(finance_client, ORDERS, "o1.csv", "order", channel_id=sources["channel"])
    next_file = ORDERS.replace(b"ORD-W-204118", b"ORD-A").replace(b"ORD-W-204377", b"ORD-B")
    next_file += b"ORD-C,03/10/2026,500.00,COD,In Transit,AWB9,,,1\n"
    file = uploaded(finance_client, next_file, "o2.csv", "order", channel_id=sources["channel"])
    draft = draft_for(finance_client, file["id"])
    assert draft["provider"] == "saved"
    assert draft["field_details"]["delivery_status"]["value_map"] == {
        "Delivered": "delivered", "RTO": "rto", "In Transit": "in_transit",
    }


# --- Duplicates and repeat imports -----------------------------------------------------


def test_starting_an_import_twice_does_nothing_new(finance_client, db, sources):
    file = uploaded(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    first = start_import(finance_client, file["id"], spec, job).json()
    second = start_import(finance_client, file["id"], spec, job).json()
    assert first == {"import_id": first["import_id"], "created": True}
    assert second == {"import_id": first["import_id"], "created": False}
    assert run_until_idle() == 1
    assert db.scalar(select(func.count()).select_from(Order)) == 2
    assert db.scalar(select(func.count()).select_from(MappingVersion)) == 1


def test_validating_the_same_mapping_twice_reuses_the_job(finance_client, db, sources):
    file = uploaded(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    first = validate(finance_client, file["id"], spec)
    second = validate(finance_client, file["id"], spec)
    assert first["id"] == second["id"]


def test_an_import_requires_validation_of_that_exact_mapping(finance_client, db, sources):
    file = uploaded(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    changed = {**spec, "fields": {**spec["fields"]}}
    del changed["fields"]["tracking_id"]
    response = start_import(finance_client, file["id"], changed, job)
    assert response.status_code == 409
    assert response.json()["code"] == "validation_required"


def test_rows_already_imported_from_another_file_are_not_counted_twice(
    finance_client, db, sources
):
    imported(finance_client, COD, "cod-oct.csv", "cod_remittance", partner_id=sources["courier"])
    overlapping = COD + b"AWB1004,500.00,470.00,16/10/2026\n"
    file = uploaded(finance_client, overlapping, "cod-oct-v2.csv", "cod_remittance", partner_id=sources["courier"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    job = validate(finance_client, file["id"], spec)
    assert job["result"]["rows_existing"] == 2  # warned about before importing
    assert job["result"]["existing_behaviour"] == "skip"
    import_id = start_import(finance_client, file["id"], spec, job).json()["import_id"]
    run_until_idle()
    result = finance_client.get(f"/api/v1/imports/{import_id}").json()
    assert result["rows_inserted"] == 1
    assert result["rows_duplicate"] == 2
    assert db.scalar(select(func.count()).select_from(CodRemittance)) == 3
    codes = {item["code"] for item in
             finance_client.get(f"/api/v1/imports/{import_id}/errors").json()["items"]}
    assert codes == {"duplicate_existing"}


def test_a_later_order_file_updates_status_without_losing_data(finance_client, db, sources):
    imported(finance_client, ORDERS, "o1.csv", "order", channel_id=sources["channel"])
    update = (
        b"Order No,Order Date,Order Value,Payment Mode,Status,AWB No,Courier,Delivered On,Qty\n"
        b"ORD-W-204377,02/10/2026,2398.00,Prepaid,Delivered,,,08/10/2026,\n"  # status changed
        b"ORD-W-204118,01/10/2026,1499.00,COD,Delivered,AWB1001,Courier Aggregator A,05/10/2026,1\n"
        b"ORD-NEW,03/10/2026,500.00,COD,In Transit,AWB9,,,1\n"
    )
    result = imported(finance_client, update, "o2.csv", "order", channel_id=sources["channel"])
    assert result["rows_inserted"] == 1
    assert result["rows_updated"] == 1
    assert result["rows_duplicate"] == 1  # identical, so it isn't rewritten
    db.expire_all()
    changed = db.scalar(select(Order).where(Order.external_order_id == "ORD-W-204377"))
    assert changed.delivery_status == "delivered"
    assert changed.delivered_at == date(2026, 10, 8)
    assert changed.tracking_id == "AWB1002"  # a blank cell doesn't erase what's known
    assert changed.quantity == 2
    assert changed.source_file_id == result["file"]["id"]


def test_the_same_order_id_on_two_channels_stays_separate(finance_client, db, sources):
    from app.models import SalesChannel

    other = SalesChannel(code="MKT1", name="Marketplace 1")
    db.add(other)
    db.commit()
    imported(finance_client, ORDERS, "a.csv", "order", channel_id=sources["channel"])
    imported(finance_client, ORDERS + b"\n", "b.csv", "order", channel_id=other.id)
    assert db.scalar(select(func.count()).select_from(Order)) == 4


# --- Saved mappings --------------------------------------------------------------------


def test_an_approved_mapping_is_saved_and_reused(finance_client, db, sources):
    first = imported(finance_client, COD, "a.csv", "cod_remittance", partner_id=sources["courier"])
    assert first["mapping_version"] == 1

    next_month = COD + b"AWB2,1,1,01/11/2026\n"
    file = uploaded(finance_client, next_month, "b.csv", "cod_remittance", partner_id=sources["courier"])
    draft = draft_for(finance_client, file["id"])
    assert draft["provider"] == "saved"
    assert draft["saved_version"] == 1
    assert draft["headers_changed"] is False
    assert {c["selected_field"] for c in draft["columns"]} == {
        "tracking_id", "cod_amount", "net_remittance", "remittance_date",
    }
    spec = spec_from_draft(draft)
    job = validate(finance_client, file["id"], spec)
    import_id = start_import(finance_client, file["id"], spec, job).json()["import_id"]
    run_until_idle()
    # Reusing it unchanged doesn't create a new version.
    assert finance_client.get(f"/api/v1/imports/{import_id}").json()["mapping_version"] == 1
    assert db.scalar(select(func.count()).select_from(MappingVersion)) == 1


def test_changed_headers_force_a_fresh_review(finance_client, db, sources):
    imported(finance_client, COD, "a.csv", "cod_remittance", partner_id=sources["courier"])
    changed = COD.replace(b"Net_Rem", b"Net_Remitted").replace(b"AWB1001", b"AWB7")
    file = uploaded(finance_client, changed, "b.csv", "cod_remittance", partner_id=sources["courier"])
    draft = draft_for(finance_client, file["id"])
    assert draft["provider"] == "heuristic"
    assert draft["headers_changed"] is True


def test_mappings_are_kept_per_source_and_file_type(finance_client, db, sources):
    imported(finance_client, COD, "a.csv", "cod_remittance", partner_id=sources["courier"])
    imported(finance_client, FREIGHT, "b.csv", "freight_invoice", partner_id=sources["courier"])
    imported(finance_client, ORDERS, "c.csv", "order", channel_id=sources["channel"])
    assert db.scalar(select(func.count()).select_from(ColumnMapping)) == 3
    listed = finance_client.get("/api/v1/mappings").json()
    assert {item["source_name"] for item in listed} == {"Courier Aggregator A", "Website"}


def test_versions_are_preserved_and_can_be_restored(finance_client, db, sources, users):
    imported(finance_client, COD, "a.csv", "cod_remittance", partner_id=sources["courier"])
    next_file = COD + b"AWB2,1,1,01/11/2026\n"
    file = uploaded(finance_client, next_file, "b.csv", "cod_remittance", partner_id=sources["courier"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    del spec["fields"]["cod_amount"]  # a person decides not to import this column
    job = validate(finance_client, file["id"], spec)
    start_import(finance_client, file["id"], spec, job)
    run_until_idle()

    mapping_id = finance_client.get("/api/v1/mappings").json()[0]["id"]
    versions = finance_client.get(f"/api/v1/mappings/{mapping_id}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["is_active"] for v in versions] == [True, False]
    assert "cod_amount" in versions[1]["spec"]["fields"]

    restored = finance_client.post(f"/api/v1/mappings/{mapping_id}/versions/1/restore")
    assert restored.status_code == 201
    assert restored.json()["version"] == 3  # a new version, leaving history untouched
    versions = finance_client.get(f"/api/v1/mappings/{mapping_id}/versions").json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert "cod_amount" in versions[0]["spec"]["fields"]
    again = finance_client.post(f"/api/v1/mappings/{mapping_id}/versions/3/restore")
    assert again.status_code == 409


# --- Mapping checks --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda s: s["fields"].pop("order_id"), "Order ID is required"),
        (lambda s: s["fields"]["order_date"].update(date_format=None), "date format"),
        (lambda s: s["fields"]["order_date"].update(date_format="whenever"), "supported"),
        (lambda s: s["fields"].update(made_up={"column": "AWB No"}), "isn't a field"),
        (lambda s: s["fields"]["tracking_id"].update(column="Nope"), "isn't in this file"),
        (lambda s: s["fields"]["tracking_id"].update(column="Order No"), "mapped to both"),
        (lambda s: s["fields"]["payment_method"]["value_map"].update(COD="crypto"), "doesn't allow"),
        (lambda s: s["fields"]["order_id"].update(negate=True), "sign reversed"),
    ],
)
def test_bad_mappings_are_refused_before_any_work(finance_client, db, sources, change, fragment):
    file = uploaded(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    change(spec)
    response = finance_client.post(f"{FILES}/{file['id']}/validations", json={"spec": spec})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_mapping"
    assert any(fragment in problem for problem in response.json()["details"])
    jobs = db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.kind == "validation"))
    assert jobs == 0


def test_any_of_groups_require_a_way_to_link_records(finance_client, sources):
    file = uploaded(finance_client, COD, "c.csv", "cod_remittance", partner_id=sources["courier"])
    spec = spec_from_draft(draft_for(finance_client, file["id"]))
    del spec["fields"]["tracking_id"]
    response = finance_client.post(f"{FILES}/{file['id']}/validations", json={"spec": spec})
    assert response.status_code == 422
    assert "linked to orders" in response.json()["message"]


# --- History and audit -----------------------------------------------------------------


def test_history_shows_what_was_uploaded_and_how_it_went(
    finance_client, founder_client, sources
):
    done = imported(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    waiting = uploaded(finance_client, COD, "c.csv", "cod_remittance", partner_id=sources["courier"])
    failed = uploaded(finance_client, b"a,b\n", "bad.csv", "order", channel_id=sources["channel"])

    page = founder_client.get(f"{FILES}?limit=10").json()  # the co-founder can see it all
    assert page["total"] == 3
    by_id = {item["id"]: item for item in page["items"]}
    item = by_id[done["file"]["id"]]
    assert item["filename"] == "o.csv"
    assert item["source_name"] == "Website"
    assert item["file_type"] == "order"
    assert item["uploaded_by"] == "finance@test.example"
    assert item["stage"] == "imported"
    assert item["import_job"]["rows_total"] == 2
    assert item["import_job"]["rows_inserted"] == 2
    assert by_id[waiting["id"]]["stage"] == "needs_mapping"
    assert by_id[waiting["id"]]["source_name"] == "Courier Aggregator A"
    assert by_id[failed["id"]]["stage"] == "analysis_failed"
    assert by_id[failed["id"]]["error"]

    filtered = founder_client.get(f"{FILES}?file_type=order").json()
    assert filtered["total"] == 2


def test_important_actions_are_audited(finance_client, db, sources, users):
    result = imported(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    logs = db.scalars(select(AuditLog).where(AuditLog.entity_type.in_(["file", "mapping"]))).all()
    actions = {log.action: log for log in logs}
    assert {"file.upload", "mapping.approve", "import.start", "import.complete"} <= set(actions)
    assert all(log.actor_id == users["finance"].id for log in logs)
    assert actions["file.upload"].after["sha256"] == sha256(ORDERS)
    assert actions["mapping.approve"].after["version"] == 1
    assert actions["import.complete"].after["inserted"] == 2
    assert result["file"]["id"] == int(actions["import.complete"].entity_id)
    # File contents never reach the audit log.
    assert "ORD-W-204118" not in str([log.after for log in logs])


def test_import_timings_are_recorded(finance_client, sources):
    result = imported(finance_client, ORDERS, "o.csv", "order", channel_id=sources["channel"])
    assert {"parse", "validate", "load"} <= set(result["timings_ms"])
    assert result["started_at"] and result["finished_at"]
    assert result["attempts"] == 1
