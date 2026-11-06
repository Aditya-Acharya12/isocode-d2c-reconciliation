"""Helpers for driving the ingestion workflow through the real API."""

import hashlib
import io

import xlsxwriter

from app.models import Partner, SalesChannel
from app.models.enums import PartnerType
from app.services.jobs import run_until_idle

FILES = "/api/v1/files"


def seed_sources(db) -> dict:
    partners = {
        "marketplace": Partner(
            name="Marketplace 1", type=PartnerType.MARKETPLACE,
            settlement_terms_days=15, return_grace_days=15,
        ),
        "courier": Partner(
            name="Courier Aggregator A", type=PartnerType.COURIER,
            settlement_terms_days=10, return_grace_days=15,
        ),
        "warehouse": Partner(
            name="Warehouse", type=PartnerType.WAREHOUSE,
            settlement_terms_days=0, return_grace_days=0,
        ),
    }
    channel = SalesChannel(code="WEB", name="Website")
    db.add_all([*partners.values(), channel])
    db.commit()
    return {**{key: partner.id for key, partner in partners.items()}, "channel": channel.id}


def upload(client, content: bytes, name: str, file_type: str, **source):
    data = {"file_type": file_type, **{key: str(value) for key, value in source.items()}}
    return client.post(FILES, files={"file": (name, content)}, data=data)


def uploaded(client, content: bytes, name: str, file_type: str, **source) -> dict:
    """Uploads a file and runs its analysis."""
    response = upload(client, content, name, file_type, **source)
    assert response.status_code == 201, response.text
    run_until_idle()
    return client.get(f"{FILES}/{response.json()['id']}").json()


def draft_for(client, file_id: int) -> dict:
    response = client.post(f"{FILES}/{file_id}/mapping-suggestions")
    assert response.status_code == 200, response.text
    return response.json()


def spec_from_draft(draft: dict, *, include_suggested: bool = False) -> dict:
    """What a person gets by accepting the form as it's first shown."""
    fields = {}
    for column in draft["columns"]:
        name = column["selected_field"] or (column["suggested_field"] if include_suggested else None)
        if name and name in draft["field_details"]:
            detail = draft["field_details"][name]
            fields[name] = {key: value for key, value in detail.items() if value is not None}
    return {"sheet": draft["sheet"], "header_row": draft["header_row"], "fields": fields}


def validate(client, file_id: int, spec: dict) -> dict:
    response = client.post(f"{FILES}/{file_id}/validations", json={"spec": spec})
    assert response.status_code == 202, response.text
    run_until_idle()
    return client.get(f"/api/v1/jobs/{response.json()['id']}").json()


def start_import(client, file_id: int, spec: dict, job: dict, acknowledged: int | None = None):
    return client.post(
        f"{FILES}/{file_id}/imports",
        json={
            "spec": spec,
            "validation_job_id": job["id"],
            "acknowledged_invalid_rows": (
                job["result"]["rows_invalid"] if acknowledged is None else acknowledged
            ),
            "suggested_by": "heuristic",
        },
    )


def imported(client, content: bytes, name: str, file_type: str, **source) -> dict:
    """The whole workflow, accepting the suggested mapping."""
    file = uploaded(client, content, name, file_type, **source)
    assert file["stage"] == "needs_mapping", file
    spec = spec_from_draft(draft_for(client, file["id"]))
    job = validate(client, file["id"], spec)
    assert job["status"] == "succeeded", job
    response = start_import(client, file["id"], spec, job)
    assert response.status_code == 202, response.text
    run_until_idle()
    return client.get(f"/api/v1/imports/{response.json()['import_id']}").json()


def workbook(rows: list[list], *, sheet: str = "Sheet1", extra_sheet_first: bool = False) -> bytes:
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True, "default_date_format": "dd/mm/yyyy"})
    if extra_sheet_first:
        book.add_worksheet("Empty")
    worksheet = book.add_worksheet(sheet)
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            worksheet.write(row_index, column_index, value)
    book.close()
    return buffer.getvalue()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

