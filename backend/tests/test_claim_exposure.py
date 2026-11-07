"""How much of an open discrepancy is still at risk once its claim is counted.

Pure arithmetic, no database. The end-to-end cases are in test_reconciliation_flow.py.
"""

from decimal import Decimal

import pytest

from app.models.enums import ClaimStatus
from app.services.reconciliation.claims import split_exposure

D = Decimal


@pytest.mark.parametrize(
    ("current", "claimed", "recovered", "status", "expected"),
    [
        # No claim, or only a rejected one: all of it is at risk.
        ("37.00", "0", "0", None, ("37.00", "0")),
        ("37.00", "37.00", "0", ClaimStatus.REJECTED, ("37.00", "0")),
        # Filed but nothing recovered yet.
        ("37.00", "37.00", "0", ClaimStatus.DRAFT, ("37.00", "0")),
        ("37.00", "37.00", "0", ClaimStatus.SUBMITTED, ("37.00", "0")),
        # Partly recovered, entered by hand only.
        ("37.00", "37.00", "20.00", ClaimStatus.PARTIALLY_RECOVERED, ("17.00", "0")),
        # The same 20 later arrives in a settlement file too: subtracted once, not twice.
        ("17.00", "37.00", "20.00", ClaimStatus.PARTIALLY_RECOVERED, ("17.00", "0")),
        # Data shows more than was entered by hand: the data wins.
        ("10.00", "37.00", "20.00", ClaimStatus.PARTIALLY_RECOVERED, ("10.00", "0")),
        # Fully recovered: nothing at risk, whether or not the data shows it yet.
        ("37.00", "37.00", "37.00", ClaimStatus.RECOVERED, ("0", "0")),
        ("7.00", "37.00", "37.00", ClaimStatus.RECOVERED, ("0", "0")),
        # Recovered always means the full claim (the transition enforces it). Even if an
        # older row said otherwise, nothing is written off without an explicit write-off.
        ("37.00", "37.00", "30.00", ClaimStatus.RECOVERED, ("7.00", "0")),
        # Written off: out of money at risk, reported separately.
        ("37.00", "37.00", "0", ClaimStatus.WRITTEN_OFF, ("0", "37.00")),
        ("37.00", "37.00", "20.00", ClaimStatus.WRITTEN_OFF, ("0", "17.00")),
        # A shortfall that grew after a closed claim: the growth is still at risk.
        ("50.00", "37.00", "37.00", ClaimStatus.RECOVERED, ("13.00", "0")),
        ("50.00", "37.00", "0", ClaimStatus.WRITTEN_OFF, ("13.00", "37.00")),
    ],
)
def test_split_exposure(current, claimed, recovered, status, expected):
    at_risk, written_off = split_exposure(D(current), D(claimed), D(recovered), status)
    assert (at_risk, written_off) == (D(expected[0]), D(expected[1]))


def test_never_negative():
    for current in ("0", "5.00", "37.00", "60.00"):
        for recovered in ("0", "20.00", "37.00"):
            for status in ClaimStatus:
                at_risk, written_off = split_exposure(D(current), D("37.00"), D(recovered), status)
                assert at_risk >= 0 and written_off >= 0
                # Together they never exceed what the latest run still sees.
                assert at_risk + written_off <= D(current)
