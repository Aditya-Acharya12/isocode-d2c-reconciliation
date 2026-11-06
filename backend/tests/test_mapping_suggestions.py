"""Suggestions must help without ever deciding anything, whichever provider made them."""

import ast
import json
import subprocess
import sys
from unittest.mock import patch

import httpx
import pytest

from app.core.config import get_settings
from app.models.enums import FileType
from app.services.ingestion import suggest
from app.services.ingestion.canonical import SCHEMAS
from app.services.ingestion.spec import header_fingerprint
from app.services.ingestion.suggest import (
    GeminiSuggester,
    HeuristicSuggester,
    MappingContext,
    build_draft,
    mask_samples,
    suggest_value_map,
)
from tests.conftest import BACKEND_DIR


def _context(file_type: FileType, columns: list[str], samples: dict | None = None):
    samples = samples or {}
    return MappingContext(
        file_type, columns, {column: {"samples": samples.get(column, [])} for column in columns}
    )


def _mapped(file_type: FileType, columns: list[str], samples: dict | None = None) -> dict:
    results = HeuristicSuggester().suggest(_context(file_type, columns, samples))
    return {item.column: item.field for item in results}


def test_the_examples_from_the_brief():
    mapped = _mapped(FileType.COD_REMITTANCE, ["AWB_No", "COD_Collected", "Net_Rem"])
    assert mapped == {
        "AWB_No": "tracking_id",
        "COD_Collected": "cod_amount",
        "Net_Rem": "net_remittance",
    }
    assert _mapped(FileType.FREIGHT_INVOICE, ["Wt_Slab"])["Wt_Slab"] == "weight_slab"


@pytest.mark.parametrize(
    ("file_type", "column", "field"),
    [
        (FileType.ORDER, "Order Number", "order_id"),
        (FileType.ORDER, "order-date", "order_date"),
        (FileType.ORDER, "PAYMENT MODE", "payment_method"),
        (FileType.SETTLEMENT, "Settled Amount", "net_amount"),
        (FileType.SETTLEMENT, "TCS", "tcs_amount"),
        (FileType.SETTLEMENT, "TDS Deducted", "tds_amount"),
        (FileType.FREIGHT_INVOICE, "Chargeable Weight", "billed_weight_grams"),
        (FileType.WAREHOUSE_RECEIPT, "GRN No", "receipt_ref"),
        (FileType.RETURN_RECORD, "RTO Date", "returned_at"),
    ],
)
def test_common_partner_headers(file_type, column, field):
    assert _mapped(file_type, [column])[column] == field


def test_unrelated_columns_are_flagged_as_unsupported():
    results = HeuristicSuggester().suggest(
        _context(FileType.ORDER, ["Customer Favourite Colour"])
    )
    assert results[0].field is None
    assert results[0].confidence == 0.0
    assert "won't be imported" in results[0].reason


def test_each_field_goes_to_one_column_and_the_loser_is_flagged():
    results = {
        item.column: item
        for item in HeuristicSuggester().suggest(
            _context(FileType.SETTLEMENT, ["Net Amount", "Net Payable"])
        )
    }
    fields = [item.field for item in results.values() if item.field]
    assert fields == ["net_amount"]
    assert any(item.ambiguous for item in results.values())


def test_data_that_contradicts_a_name_lowers_confidence():
    good = HeuristicSuggester().suggest(
        _context(FileType.ORDER, ["Order Value"], {"Order Value": ["1499", "899.50"]})
    )[0]
    bad = HeuristicSuggester().suggest(
        _context(FileType.ORDER, ["Order Value"], {"Order Value": ["pending", "unknown"]})
    )[0]
    assert bad.confidence < good.confidence
    assert bad.confidence < 0.8


def _detection(columns, samples=None, distinct=None, date_format=None) -> dict:
    samples = samples or {}
    distinct = distinct or {}
    return {
        "columns": columns,
        "header_row": 0,
        "sheet": None,
        "profiles": {
            column: {
                "samples": samples.get(column, []),
                "distinct": distinct.get(column, []),
                "date_format": date_format,
                "empty_count": 0,
            }
            for column in columns
        },
    }


def test_low_confidence_and_ambiguous_suggestions_are_never_preselected():
    draft = build_draft(
        FileType.SETTLEMENT,
        _detection(["Net Amount", "Net Payable", "Settlmnt Ref No"]),
    )
    by_column = {row["column"]: row for row in draft["columns"]}
    for row in by_column.values():
        if row["ambiguous"] or row["confidence"] < 0.8:
            assert row["selected_field"] is None, row
    assert draft["provider"] == "heuristic"


def test_value_maps_are_suggested_but_unknown_values_stay_unmapped():
    field = SCHEMAS[FileType.ORDER].get("delivery_status")
    mapping = suggest_value_map(field, ["DLVD", "RTO-Delivered", "Mystery"])
    assert mapping == {"DLVD": "delivered", "RTO-Delivered": "rto"}


# --- Gemini ---------------------------------------------------------------------------


def _gemini_reply(columns: list[dict]) -> httpx.Response:
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"columns": columns})}]}}]}
    return httpx.Response(200, json=body, request=httpx.Request("POST", "https://x"))


COLUMNS = ["AWB_No", "Net_Rem", "Mystery"]


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", "fake-test-key")


def test_no_key_means_rules_and_no_network_call():
    with patch.object(httpx, "post", side_effect=AssertionError("network used")):
        _, provider, note = suggest.suggest_columns(_context(FileType.COD_REMITTANCE, COLUMNS))
    assert provider == "heuristic"
    assert note is None


def test_a_valid_gemini_answer_is_used_but_only_as_a_suggestion(with_key):
    reply = _gemini_reply(
        [
            {"column": "AWB_No", "field": "tracking_id", "confidence": 0.95, "reason": "AWB",
             "ambiguous": False},
            {"column": "Net_Rem", "field": "net_remittance", "confidence": 0.6, "reason": "maybe",
             "ambiguous": False},
            {"column": "Mystery", "field": None, "confidence": 0.1, "reason": "unknown",
             "ambiguous": False},
        ]
    )
    with patch.object(httpx, "post", return_value=reply) as post:
        draft = build_draft(FileType.COD_REMITTANCE, _detection(COLUMNS))
    assert draft["provider"] == "gemini"
    assert draft["note"] is None
    by_column = {row["column"]: row for row in draft["columns"]}
    assert by_column["AWB_No"]["selected_field"] == "tracking_id"
    assert by_column["Net_Rem"]["selected_field"] is None  # too unsure to preselect
    assert by_column["Net_Rem"]["suggested_field"] == "net_remittance"
    # The key travels in a header, never in the URL, where logs would capture it.
    assert "fake-test-key" not in post.call_args.args[0]
    assert post.call_args.kwargs["headers"]["x-goog-api-key"] == "fake-test-key"
    config = post.call_args.kwargs["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["columns"]


@pytest.mark.parametrize(
    "columns",
    [
        [{"column": "AWB_No", "field": "tracking_id", "confidence": 0.9, "reason": "x",
          "ambiguous": False}],  # doesn't cover every column
        [
            {"column": c, "field": "invented_field", "confidence": 0.9, "reason": "x",
             "ambiguous": False}
            for c in COLUMNS
        ],
        [
            {"column": c, "field": "tracking_id", "confidence": 0.9, "reason": "x",
             "ambiguous": False}
            for c in COLUMNS
        ],  # one field used three times
        [
            {"column": c, "field": None, "confidence": 7, "reason": "x", "ambiguous": False}
            for c in COLUMNS
        ],  # an impossible confidence
    ],
)
def test_a_bad_gemini_answer_is_rejected_whole(with_key, columns):
    with patch.object(httpx, "post", return_value=_gemini_reply(columns)):
        _, provider, note = suggest.suggest_columns(_context(FileType.COD_REMITTANCE, COLUMNS))
    assert provider == "heuristic"
    assert "Gemini was unavailable" in note


@pytest.mark.parametrize(
    "failure",
    [
        httpx.TimeoutException("slow"),
        httpx.ConnectError("no route"),
    ],
)
def test_gemini_outages_fall_back_to_rules(with_key, failure):
    with patch.object(httpx, "post", side_effect=failure):
        results, provider, note = suggest.suggest_columns(
            _context(FileType.COD_REMITTANCE, COLUMNS)
        )
    assert provider == "heuristic"
    assert note
    assert {item.column: item.field for item in results}["AWB_No"] == "tracking_id"


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(429, request=httpx.Request("POST", "https://x")),
        httpx.Response(200, json={"candidates": []}, request=httpx.Request("POST", "https://x")),
        httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
            request=httpx.Request("POST", "https://x"),
        ),
    ],
)
def test_bad_gemini_responses_fall_back_to_rules(with_key, reply):
    with patch.object(httpx, "post", return_value=reply):
        _, provider, _ = suggest.suggest_columns(_context(FileType.COD_REMITTANCE, COLUMNS))
    assert provider == "heuristic"


def test_personal_data_never_reaches_the_provider():
    assert mask_samples("Customer Name", ["Asha Rao"]) == ["[masked]"]
    assert mask_samples("Buyer Phone", ["9876543210"]) == ["[masked]"]
    assert mask_samples("Shipping Address", ["12 MG Road"]) == ["[masked]"]
    assert mask_samples("Notes", ["a@b.co", "+91 98765 43210", "ok"]) == [
        "[masked]", "[masked]", "ok",
    ]
    assert mask_samples("AWB No", ["AWB1001"]) == ["AWB1001"]

    gemini = GeminiSuggester("k", "m")
    prompt = gemini._prompt(
        _context(FileType.ORDER, ["Customer Name"], {"Customer Name": ["Asha Rao"]}),
        SCHEMAS[FileType.ORDER],
    )
    assert "Asha Rao" not in prompt


def test_saved_mappings_are_reused_without_calling_a_provider(with_key):
    columns = ["AWB_No", "Net_Rem"]
    saved = {"fields": {"tracking_id": {"column": "AWB_No"}, "net_remittance": {"column": "Net_Rem"}}}
    with patch.object(httpx, "post", side_effect=AssertionError("network used")):
        draft = build_draft(FileType.COD_REMITTANCE, _detection(columns), saved, 4, True)
    assert draft["provider"] == "saved"
    assert draft["saved_version"] == 4


def test_fingerprints_ignore_order_and_punctuation_but_not_names():
    assert header_fingerprint(["AWB No", "Net"]) == header_fingerprint(["net", "awb_no"])
    assert header_fingerprint(["AWB No", "Net"]) != header_fingerprint(["AWB No", "Gross"])


# --- Boundaries -----------------------------------------------------------------------


def test_suggestions_cannot_reach_the_code_that_handles_rows():
    """AI may fill in a form. It must have no path to imports or calculations."""
    for name in ("pipeline", "handlers", "parsing", "canonical", "spec"):
        tree = ast.parse((BACKEND_DIR / "app/services/ingestion" / f"{name}.py").read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "suggest" not in (node.module or ""), name
            if isinstance(node, ast.Import):
                assert not any("suggest" in alias.name or "httpx" in alias.name
                               for alias in node.names), name


def test_the_api_starts_without_loading_pandas():
    """Keeps cold starts short and idle memory low on a small instance."""
    code = "import sys, app.main; print('pandas' in sys.modules)"
    output = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert output.stdout.strip() == "False"
