"""Creates demo users, partners, channels and default settings. Safe to run repeatedly.

Every name is fictional. Passwords come from the environment, so none live in the repo.
Existing rows are left unchanged, except demo passwords, which follow the environment.
"""

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


def seed(db: Session, finance_password: str, founder_password: str) -> None:
    users = [
        (FINANCE_EMAIL, "Finance Executive (Demo)", Role.FINANCE_EXECUTIVE, finance_password),
        (FOUNDER_EMAIL, "Co-founder (Demo)", Role.CO_FOUNDER, founder_password),
    ]
    for email, full_name, role, password in users:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(
                User(
                    email=email,
                    full_name=full_name,
                    role=role,
                    password_hash=hash_password(password),
                )
            )
        else:
            user.password_hash = hash_password(password)

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

    db.commit()


def main() -> None:
    load_dotenv(BACKEND_DIR / ".env")
    finance_password = _required_env("SEED_FINANCE_PASSWORD")
    founder_password = _required_env("SEED_FOUNDER_PASSWORD")
    with Session(get_direct_engine()) as db:
        seed(db, finance_password, founder_password)
    print(f"Seeded. Sign in as {FINANCE_EMAIL} or {FOUNDER_EMAIL}.")


if __name__ == "__main__":
    main()
