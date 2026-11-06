"""Support the ingestion workflow

Revision ID: 0002
Revises: 0001

- Files and saved mappings come from a partner or a sales channel. Website orders
  have a channel but no partner.
- Warehouses become a partner type, because warehouse receipts need a source.
- Jobs can also analyse and validate files, and keep a result.
- Row issues gain a severity and message, so warnings aren't reported as failures.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check(table: str, name: str, values: tuple[str, ...], column: str) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, f"{column} IN ({allowed})")


PARTNER_TYPES = ("marketplace", "quick_commerce", "courier", "payment_gateway")
JOB_KINDS = ("import", "reconciliation")


def upgrade() -> None:
    _replace_check("partners", "partner_type", (*PARTNER_TYPES, "warehouse"), "type")
    _replace_check(
        "background_jobs", "job_kind", ("file_analysis", "validation", *JOB_KINDS), "kind"
    )
    op.add_column(
        "background_jobs", sa.Column("result", postgresql.JSONB(astext_type=sa.Text()))
    )

    op.add_column("uploaded_files", sa.Column("channel_id", sa.Integer()))
    op.add_column(
        "uploaded_files", sa.Column("detection", postgresql.JSONB(astext_type=sa.Text()))
    )
    op.alter_column("uploaded_files", "partner_id", nullable=True)
    op.create_foreign_key(
        op.f("fk_uploaded_files_channel_id_sales_channels"),
        "uploaded_files",
        "sales_channels",
        ["channel_id"],
        ["id"],
    )
    op.drop_constraint(
        "uq_uploaded_files_partner_id_file_type_sha256", "uploaded_files", type_="unique"
    )
    op.create_unique_constraint(
        "uq_uploaded_files_source_content",
        "uploaded_files",
        ["partner_id", "channel_id", "file_type", "sha256"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        op.f("ck_uploaded_files_one_source"), "uploaded_files", "num_nonnulls(partner_id, channel_id) = 1"
    )

    op.add_column("column_mappings", sa.Column("channel_id", sa.Integer()))
    op.alter_column("column_mappings", "partner_id", nullable=True)
    op.create_foreign_key(
        op.f("fk_column_mappings_channel_id_sales_channels"),
        "column_mappings",
        "sales_channels",
        ["channel_id"],
        ["id"],
    )
    op.drop_constraint(
        "uq_column_mappings_partner_id_file_type", "column_mappings", type_="unique"
    )
    op.create_unique_constraint(
        "uq_column_mappings_source_type",
        "column_mappings",
        ["partner_id", "channel_id", "file_type"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        op.f("ck_column_mappings_one_source"),
        "column_mappings",
        "num_nonnulls(partner_id, channel_id) = 1",
    )

    op.create_unique_constraint(op.f("uq_import_jobs_file_id"), "import_jobs", ["file_id"])
    for column in ("rows_excluded", "rows_warned"):
        op.add_column("import_jobs", sa.Column(column, sa.Integer()))
    op.add_column("import_jobs", sa.Column("summary", postgresql.JSONB(astext_type=sa.Text())))

    op.add_column(
        "import_row_errors",
        sa.Column("severity", sa.String(length=32), server_default="error", nullable=False),
    )
    op.alter_column("import_row_errors", "severity", server_default=None)
    op.create_check_constraint(
        op.f("ck_import_row_errors_import_issue_severity"),
        "import_row_errors",
        "severity IN ('error', 'warning', 'duplicate')",
    )
    op.add_column("import_row_errors", sa.Column("message", sa.Text()))


def downgrade() -> None:
    op.drop_column("import_row_errors", "message")
    op.drop_constraint(
        op.f("ck_import_row_errors_import_issue_severity"), "import_row_errors", type_="check"
    )
    op.drop_column("import_row_errors", "severity")

    op.drop_column("import_jobs", "summary")
    for column in ("rows_warned", "rows_excluded"):
        op.drop_column("import_jobs", column)
    op.drop_constraint(op.f("uq_import_jobs_file_id"), "import_jobs", type_="unique")

    # Rows with no partner can't exist under the old schema.
    op.execute("DELETE FROM import_jobs WHERE file_id IN "
               "(SELECT id FROM uploaded_files WHERE partner_id IS NULL)")
    op.execute("UPDATE column_mappings SET active_version_id = NULL WHERE partner_id IS NULL")
    op.execute("DELETE FROM mapping_versions WHERE mapping_id IN "
               "(SELECT id FROM column_mappings WHERE partner_id IS NULL)")
    op.execute("DELETE FROM column_mappings WHERE partner_id IS NULL")
    op.execute("DELETE FROM orders WHERE source_file_id IN "
               "(SELECT id FROM uploaded_files WHERE partner_id IS NULL)")
    op.execute("DELETE FROM uploaded_files WHERE partner_id IS NULL")

    op.drop_constraint(op.f("ck_column_mappings_one_source"), "column_mappings", type_="check")
    op.drop_constraint("uq_column_mappings_source_type", "column_mappings", type_="unique")
    op.create_unique_constraint(
        "uq_column_mappings_partner_id_file_type", "column_mappings", ["partner_id", "file_type"]
    )
    op.drop_constraint(
        op.f("fk_column_mappings_channel_id_sales_channels"), "column_mappings", type_="foreignkey"
    )
    op.alter_column("column_mappings", "partner_id", nullable=False)
    op.drop_column("column_mappings", "channel_id")

    op.drop_constraint(op.f("ck_uploaded_files_one_source"), "uploaded_files", type_="check")
    op.drop_constraint("uq_uploaded_files_source_content", "uploaded_files", type_="unique")
    op.create_unique_constraint(
        "uq_uploaded_files_partner_id_file_type_sha256",
        "uploaded_files",
        ["partner_id", "file_type", "sha256"],
    )
    op.drop_constraint(
        op.f("fk_uploaded_files_channel_id_sales_channels"), "uploaded_files", type_="foreignkey"
    )
    op.alter_column("uploaded_files", "partner_id", nullable=False)
    op.drop_column("uploaded_files", "detection")
    op.drop_column("uploaded_files", "channel_id")

    op.drop_column("background_jobs", "result")
    op.execute("DELETE FROM background_jobs WHERE kind IN ('file_analysis', 'validation')")
    _replace_check("background_jobs", "job_kind", JOB_KINDS, "kind")
    op.execute("DELETE FROM partners WHERE type = 'warehouse'")
    _replace_check("partners", "partner_type", PARTNER_TYPES, "type")
