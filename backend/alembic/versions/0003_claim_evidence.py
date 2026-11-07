"""Keep each claim's evidence as it was when filed

Revision ID: 0003
Revises: 0002

Adds one nullable column. A claim's calculation and supporting records are copied in
when it's created, so later reconciliation runs can't change what was sent to a partner.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text())))


def downgrade() -> None:
    op.drop_column("claims", "evidence")
