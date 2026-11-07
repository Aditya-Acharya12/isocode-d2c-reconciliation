"""The seed script never changes credentials or configuration unless told to."""

import pytest
from sqlalchemy import func, select

from app.core.security import verify_password
from app.models import (
    FreightRateRule,
    Partner,
    RateCard,
    SalesChannel,
    Setting,
    SettlementRateRule,
    User,
)
from scripts import seed_foundation
from scripts.seed_foundation import (
    FINANCE_EMAIL,
    FOUNDER_EMAIL,
    passwords_needed,
    seed,
    seed_rate_card_only,
)

FIRST = {"SEED_FINANCE_PASSWORD": "first-finance-pw", "SEED_FOUNDER_PASSWORD": "first-founder-pw"}
OTHER = {"SEED_FINANCE_PASSWORD": "other-finance-pw", "SEED_FOUNDER_PASSWORD": "other-founder-pw"}


def _hashes(db) -> dict[str, str]:
    db.expire_all()
    return dict(db.execute(select(User.email, User.password_hash)).all())


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_a_first_seed_creates_the_demo_users(db):
    assert passwords_needed(db, reset_passwords=False) == [
        "SEED_FINANCE_PASSWORD",
        "SEED_FOUNDER_PASSWORD",
    ]
    seed(db, FIRST)
    hashes = _hashes(db)
    assert verify_password("first-finance-pw", hashes[FINANCE_EMAIL])
    assert verify_password("first-founder-pw", hashes[FOUNDER_EMAIL])
    assert db.scalar(select(RateCard.name)) == "Demo rate card"


def test_rerunning_leaves_existing_passwords_alone(db):
    seed(db, FIRST)
    before = _hashes(db)
    assert passwords_needed(db, reset_passwords=False) == []  # no password variables needed
    seed(db, OTHER)
    assert _hashes(db) == before  # not even rehashed


def test_reset_passwords_is_explicit(db):
    seed(db, FIRST)
    assert passwords_needed(db, reset_passwords=True) == [
        "SEED_FINANCE_PASSWORD",
        "SEED_FOUNDER_PASSWORD",
    ]
    seed(db, OTHER, reset_passwords=True)
    hashes = _hashes(db)
    assert verify_password("other-finance-pw", hashes[FINANCE_EMAIL])
    assert verify_password("other-founder-pw", hashes[FOUNDER_EMAIL])


def test_the_options_cant_be_combined():
    with pytest.raises(SystemExit):
        seed_foundation.main(["--rate-card-only", "--reset-passwords"])


def _foundation_without_card(db):
    """Like production before this release: users, partners and channels, but no card."""
    seed(db, FIRST)
    db.execute(SettlementRateRule.__table__.delete())
    db.execute(FreightRateRule.__table__.delete())
    db.execute(RateCard.__table__.delete())
    db.commit()


def test_rate_card_only_adds_the_card_and_changes_nothing_else(db):
    _foundation_without_card(db)
    setting = db.get(Setting, "discrepancy_threshold_paise")
    setting.value = 2500  # an edit made in the app
    db.commit()
    hashes, partners, channels = _hashes(db), _count(db, Partner), _count(db, SalesChannel)

    message = seed_rate_card_only(db)
    assert message.startswith('Added "Demo rate card" v1')
    db.expire_all()
    assert _count(db, RateCard) == 1
    rules = _count(db, SettlementRateRule)
    assert rules > 0
    assert _hashes(db) == hashes
    assert (_count(db, Partner), _count(db, SalesChannel)) == (partners, channels)
    assert db.get(Setting, "discrepancy_threshold_paise").value == 2500

    # Idempotent: a second run adds nothing.
    assert "already exists" in seed_rate_card_only(db)
    db.expire_all()
    assert (_count(db, RateCard), _count(db, SettlementRateRule)) == (1, rules)


def test_rate_card_only_never_overwrites_an_edited_card(db):
    seed(db, FIRST)
    rule = db.scalar(select(SettlementRateRule).order_by(SettlementRateRule.id))
    rule.pct_bps = 1234
    db.commit()
    assert "already exists" in seed_rate_card_only(db)
    db.expire_all()
    assert db.get(SettlementRateRule, rule.id).pct_bps == 1234


def test_rate_card_only_refuses_without_the_foundation(db):
    with pytest.raises(SystemExit, match="Run the full seed first"):
        seed_rate_card_only(db)
    assert _count(db, RateCard) == 0
    assert _count(db, User) == 0
