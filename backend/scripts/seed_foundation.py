"""Creates demo users, partners, channels, default settings and the demo rate card.

Safe to run repeatedly: it only adds what's missing, and never changes an existing row.
Every name is fictional. Passwords come from the environment, so none live in the repo.

    seed_foundation.py                    add whatever is missing
    seed_foundation.py --rate-card-only   add only the demo rate card, touching nothing else
    seed_foundation.py --reset-passwords  also set existing demo users' passwords

Without --reset-passwords, an existing demo user's password is never changed, and the
password variables are only needed when a demo user has to be created.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import BACKEND_DIR  # noqa: E402
from app.core.database import get_direct_engine  # noqa: E402
from app.core.permissions import Role  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import Partner, SalesChannel, Setting, User  # noqa: E402
from app.models.enums import PartnerType  # noqa: E402
from app.services.reconciliation.demo_rates import (  # noqa: E402
    CARD_NAME,
    missing_references,
    seed_demo_rate_card,
)

FINANCE_EMAIL = "finance@loomandfold.example"
FOUNDER_EMAIL = "founder@loomandfold.example"

# Terms are invented demo values (assumption A14), and can be edited in the app.
PARTNERS = [
    ("Marketplace 1", PartnerType.MARKETPLACE, 15, 15),
    ("Marketplace 2", PartnerType.MARKETPLACE, 15, 15),
    ("Marketplace 3", PartnerType.MARKETPLACE, 15, 15),
    ("Quick Commerce 1", PartnerType.QUICK_COMMERCE, 7, 7),
    ("Quick Commerce 2", PartnerType.QUICK_COMMERCE, 7, 7),
    ("Courier Aggregator A", PartnerType.COURIER, 10, 15),
    ("Courier Aggregator B", PartnerType.COURIER, 10, 15),
    ("Payment Gateway", PartnerType.PAYMENT_GATEWAY, 3, 15),
    ("Warehouse", PartnerType.WAREHOUSE, 0, 0),
]

CHANNELS = [
    ("WEB", "Website", None),
    ("MKT1", "Marketplace 1", "Marketplace 1"),
    ("MKT2", "Marketplace 2", "Marketplace 2"),
    ("MKT3", "Marketplace 3", "Marketplace 3"),
    ("QC1", "Quick Commerce 1", "Quick Commerce 1"),
    ("QC2", "Quick Commerce 2", "Quick Commerce 2"),
]

SETTINGS = {
    "discrepancy_threshold_paise": 1000,  # flags only differences strictly above ₹10
}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 12:
        raise SystemExit(f"{name} must be set to at least 12 characters.")
    return value


DEMO_USERS = [
    (FINANCE_EMAIL, "Finance Executive (Demo)", Role.FINANCE_EXECUTIVE, "SEED_FINANCE_PASSWORD"),
    (FOUNDER_EMAIL, "Co-founder (Demo)", Role.CO_FOUNDER, "SEED_FOUNDER_PASSWORD"),
]


def passwords_needed(db: Session, reset_passwords: bool) -> list[str]:
    """The password variables this run needs: for missing users, or all of them on a reset."""
    return [
        variable
        for email, _, _, variable in DEMO_USERS
        if reset_passwords or db.scalar(select(User.id).where(User.email == email)) is None
    ]


def seed(db: Session, passwords: dict[str, str], reset_passwords: bool = False) -> None:
    """passwords maps each variable in DEMO_USERS to its value, where one is needed."""
    for email, full_name, role, variable in DEMO_USERS:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(
                User(
                    email=email,
                    full_name=full_name,
                    role=role,
                    password_hash=hash_password(passwords[variable]),
                )
            )
        elif reset_passwords:
            user.password_hash = hash_password(passwords[variable])

    existing_partners = {p.name: p for p in db.scalars(select(Partner))}
    for name, partner_type, terms, grace in PARTNERS:
        if name not in existing_partners:
            partner = Partner(
                name=name,
                type=partner_type,
                settlement_terms_days=terms,
                return_grace_days=grace,
            )
            db.add(partner)
            existing_partners[name] = partner
    db.flush()

    existing_channels = set(db.scalars(select(SalesChannel.code)))
    for code, name, partner_name in CHANNELS:
        if code not in existing_channels:
            partner_id = existing_partners[partner_name].id if partner_name else None
            db.add(SalesChannel(code=code, name=name, partner_id=partner_id))

    for key, value in SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value))
    db.flush()

    # Only added when no card of that name exists, so editing rates later is never undone.
    seed_demo_rate_card(db)
    db.commit()


def seed_rate_card_only(db: Session) -> str:
    """Adds the demo rate card and nothing else. Users, partners, channels and settings
    are only read. Refuses, writing nothing, if the partners or channels it needs are
    missing, because a card created without them couldn't be completed by a later run."""
    missing = missing_references(db)
    if missing:
        raise SystemExit(
            "The demo rate card needs partners and channels that don't exist yet: "
            f"{', '.join(missing)}. Run the full seed first. Nothing was changed."
        )
    card = seed_demo_rate_card(db)
    if card is None:
        db.rollback()
        return f'"{CARD_NAME}" already exists, so nothing was changed.'
    db.commit()
    return f'Added "{CARD_NAME}" v{card.version}. Nothing else was changed.'


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--rate-card-only",
        action="store_true",
        help="Add only the demo rate card. Users, partners, channels and settings are untouched.",
    )
    mode.add_argument(
        "--reset-passwords",
        action="store_true",
        help="Also set existing demo users' passwords from the environment.",
    )
    args = parser.parse_args(argv)
    load_dotenv(BACKEND_DIR / ".env")
    with Session(get_direct_engine()) as db:
        if args.rate_card_only:
            print(seed_rate_card_only(db))
            return
        passwords = {
            variable: _required_env(variable)
            for variable in passwords_needed(db, args.reset_passwords)
        }
        seed(db, passwords, args.reset_passwords)
    if args.reset_passwords:
        print(f"Seeded, and reset the passwords of {FINANCE_EMAIL} and {FOUNDER_EMAIL}.")
    else:
        print(
            f"Seeded. Existing passwords were left unchanged. Users: {FINANCE_EMAIL}, "
            f"{FOUNDER_EMAIL}."
        )


if __name__ == "__main__":
    main()
