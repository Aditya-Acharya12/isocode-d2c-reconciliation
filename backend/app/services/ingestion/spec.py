"""The mapping specification: what a person approves, and what an import then follows."""

import hashlib
import json
import re

from pydantic import BaseModel, Field

from app.models.enums import FileType
from app.services.ingestion.canonical import DATE, DATE_FORMATS, ENUM, MONEY, schema_for


class FieldMapping(BaseModel):
    column: str = Field(min_length=1, max_length=255)
    date_format: str | None = None
    # Raw value in the file -> canonical value. Anything unmapped makes the row invalid.
    value_map: dict[str, str] | None = None
    # For partners that report money owed as positive, and so on.
    negate: bool = False


class MappingSpec(BaseModel):
    sheet: str | None = None
    header_row: int = Field(default=0, ge=0, le=100)
    fields: dict[str, FieldMapping]


def spec_hash(spec: MappingSpec) -> str:
    canonical = json.dumps(spec.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def normalise_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def header_fingerprint(columns: list[str]) -> str:
    """Any change in a partner's headers forces a fresh review of the saved mapping."""
    joined = "\x1f".join(sorted(normalise_header(column) for column in columns))
    return hashlib.sha256(joined.encode()).hexdigest()


def spec_problems(spec: MappingSpec, file_type: FileType, columns: list[str]) -> list[str]:
    """Everything wrong with a mapping before any row is read. Empty means it can run."""
    schema = schema_for(file_type)
    problems: list[str] = []
    known_columns = set(columns)
    used: dict[str, str] = {}

    for name, mapping in spec.fields.items():
        field = schema.get(name)
        if field is None:
            problems.append(f"'{name}' isn't a field in {schema.label.lower()} files.")
            continue
        if mapping.column not in known_columns:
            problems.append(f"Column '{mapping.column}' isn't in this file.")
        if mapping.column in used:
            problems.append(
                f"Column '{mapping.column}' is mapped to both {used[mapping.column]} and "
                f"{field.label}."
            )
        used[mapping.column] = field.label
        if field.kind == DATE:
            if not mapping.date_format:
                problems.append(f"Choose a date format for {field.label}.")
            elif mapping.date_format not in DATE_FORMATS:
                problems.append(f"'{mapping.date_format}' isn't a supported date format.")
        if field.kind == ENUM and mapping.value_map:
            for raw, value in mapping.value_map.items():
                if value not in field.enum_values:
                    problems.append(
                        f"'{raw}' maps to '{value}', which {field.label} doesn't allow."
                    )
        if mapping.negate and field.kind != MONEY:
            problems.append(f"Only money fields can have their sign reversed, not {field.label}.")

    for field in schema.fields:
        if field.required and field.name not in spec.fields:
            problems.append(f"{field.label} is required. Map a column to it.")
    for group in schema.any_of:
        if not any(name in spec.fields for name in group):
            labels = " or ".join(schema.get(name).label for name in group)
            problems.append(f"Map {labels}, so that records can be linked to orders.")
    return problems
