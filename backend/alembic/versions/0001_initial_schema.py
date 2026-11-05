"""initial schema

Revision ID: 0001
Revises: 
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lets the rate card exclusion constraint combine "=" on text with "&&" on dates.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table('partners',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('type', sa.Enum('marketplace', 'quick_commerce', 'courier', 'payment_gateway', name='partner_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('settlement_terms_days', sa.Integer(), nullable=False),
    sa.Column('return_grace_days', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('return_grace_days >= 0', name=op.f('ck_partners_grace_not_negative')),
    sa.CheckConstraint('settlement_terms_days >= 0', name=op.f('ck_partners_terms_not_negative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_partners')),
    sa.UniqueConstraint('name', name=op.f('uq_partners_name'))
    )
    op.create_table('rate_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('status', sa.Enum('draft', 'published', 'retired', name='rate_card_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('name'), '='), (sa.text("daterange(effective_from, effective_to, '[]')"), '&&'), where=sa.text("status = 'published'"), using='gist', name='ex_rate_cards_published_dates'),
    sa.CheckConstraint('effective_to IS NULL OR effective_to >= effective_from', name=op.f('ck_rate_cards_valid_dates')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rate_cards')),
    sa.UniqueConstraint('name', 'version', name=op.f('uq_rate_cards_name_version'))
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('finance_executive', 'co_founder', name='user_role', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_logs_actor_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_actor_created', 'audit_logs', ['actor_id', 'created_at'], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['entity_type', 'entity_id'], unique=False)
    op.create_table('background_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('import', 'reconciliation', name='job_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='job_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('locked_by', sa.String(length=64), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_background_jobs_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_background_jobs')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_background_jobs_idempotency_key'))
    )
    op.create_index('ix_background_jobs_claimable', 'background_jobs', ['created_at'], unique=False, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table('column_mappings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('file_type', sa.Enum('order', 'settlement', 'cod_remittance', 'freight_invoice', 'return_record', 'warehouse_receipt', name='column_mapping_file_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('active_version_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_column_mappings_partner_id_partners')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_column_mappings')),
    sa.UniqueConstraint('partner_id', 'file_type', name=op.f('uq_column_mappings_partner_id_file_type'))
    )
    op.create_table('freight_rate_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rate_card_id', sa.Integer(), nullable=False),
    sa.Column('courier_id', sa.Integer(), nullable=False),
    sa.Column('weight_slab', sa.String(length=32), nullable=False),
    sa.Column('zone', sa.String(length=32), nullable=True),
    sa.Column('shipment_type', sa.Enum('forward', 'rto', 'customer_return', name='freight_rule_shipment_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('base_paise', sa.BigInteger(), nullable=False),
    sa.Column('cod_pct_bps', sa.Integer(), nullable=False),
    sa.Column('cod_min_paise', sa.BigInteger(), nullable=False),
    sa.Column('fuel_pct_bps', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('base_paise >= 0', name=op.f('ck_freight_rate_rules_base_not_negative')),
    sa.ForeignKeyConstraint(['courier_id'], ['partners.id'], name=op.f('fk_freight_rate_rules_courier_id_partners')),
    sa.ForeignKeyConstraint(['rate_card_id'], ['rate_cards.id'], name=op.f('fk_freight_rate_rules_rate_card_id_rate_cards'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_freight_rate_rules')),
    sa.UniqueConstraint('rate_card_id', 'courier_id', 'weight_slab', 'zone', 'shipment_type', name='uq_freight_rate_rules_dimensions', postgresql_nulls_not_distinct=True)
    )
    op.create_table('sales_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('partner_id', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_sales_channels_partner_id_partners')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales_channels')),
    sa.UniqueConstraint('code', name=op.f('uq_sales_channels_code'))
    )
    op.create_table('uploaded_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('file_type', sa.Enum('order', 'settlement', 'cod_remittance', 'freight_invoice', 'return_record', 'warehouse_receipt', name='uploaded_file_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_uploaded_files_partner_id_partners')),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_uploaded_files_uploaded_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_uploaded_files')),
    sa.UniqueConstraint('partner_id', 'file_type', 'sha256', name=op.f('uq_uploaded_files_partner_id_file_type_sha256'))
    )
    op.create_table('mapping_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mapping_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('header_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('source', sa.Enum('ai', 'heuristic', 'manual', name='mapping_source', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('suggestion', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('approved_by', sa.Integer(), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name=op.f('fk_mapping_versions_approved_by_users')),
    sa.ForeignKeyConstraint(['mapping_id'], ['column_mappings.id'], name=op.f('fk_mapping_versions_mapping_id_column_mappings')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mapping_versions')),
    sa.UniqueConstraint('mapping_id', 'version', name=op.f('uq_mapping_versions_mapping_id_version'))
    )
    # The two tables reference each other, so this has to follow both of them.
    op.create_foreign_key(
        'fk_column_mappings_active_version',
        'column_mappings',
        'mapping_versions',
        ['active_version_id'],
        ['id'],
    )
    op.create_table('orders',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('external_order_id', sa.String(length=64), nullable=False),
    sa.Column('order_date', sa.Date(), nullable=False),
    sa.Column('order_value', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('payment_method', sa.Enum('cod', 'prepaid', name='order_payment_method', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('delivery_status', sa.Enum('created', 'in_transit', 'delivered', 'rto', 'customer_return', 'cancelled', name='order_delivery_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('tracking_id', sa.String(length=64), nullable=True),
    sa.Column('courier_id', sa.Integer(), nullable=True),
    sa.Column('delivered_at', sa.Date(), nullable=True),
    sa.Column('returned_at', sa.Date(), nullable=True),
    sa.Column('weight_grams', sa.Integer(), nullable=True),
    sa.Column('weight_slab', sa.String(length=32), nullable=True),
    sa.Column('zone', sa.String(length=32), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('source_row_number', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('order_value >= 0', name=op.f('ck_orders_value_not_negative')),
    sa.ForeignKeyConstraint(['channel_id'], ['sales_channels.id'], name=op.f('fk_orders_channel_id_sales_channels')),
    sa.ForeignKeyConstraint(['courier_id'], ['partners.id'], name=op.f('fk_orders_courier_id_partners')),
    sa.ForeignKeyConstraint(['source_file_id'], ['uploaded_files.id'], name=op.f('fk_orders_source_file_id_uploaded_files')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    sa.UniqueConstraint('channel_id', 'external_order_id', name=op.f('uq_orders_channel_id_external_order_id'))
    )
    op.create_index('ix_orders_status_date', 'orders', ['delivery_status', 'order_date'], unique=False)
    op.create_index('ix_orders_tracking_id', 'orders', ['tracking_id'], unique=False)
    op.create_table('reconciliation_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('scope', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='recon_run_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('background_job_id', sa.Integer(), nullable=False),
    sa.Column('triggered_by', sa.Integer(), nullable=False),
    sa.Column('rate_card_ids', postgresql.ARRAY(sa.Integer()), nullable=False),
    sa.Column('settings_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('timings_ms', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['background_job_id'], ['background_jobs.id'], name=op.f('fk_reconciliation_runs_background_job_id_background_jobs')),
    sa.ForeignKeyConstraint(['triggered_by'], ['users.id'], name=op.f('fk_reconciliation_runs_triggered_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reconciliation_runs')),
    sa.UniqueConstraint('background_job_id', name=op.f('uq_reconciliation_runs_background_job_id'))
    )
    op.create_table('settlement_rate_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rate_card_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('payment_method', sa.Enum('cod', 'prepaid', name='settlement_rule_payment_method', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('event', sa.Enum('delivered', 'rto', 'customer_return', name='settlement_rule_event', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('payer_partner_id', sa.Integer(), nullable=True),
    sa.Column('pct_bps', sa.Integer(), nullable=False),
    sa.Column('fixed_paise', sa.BigInteger(), nullable=False),
    sa.Column('round_to_paise', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('fixed_paise >= 0', name=op.f('ck_settlement_rate_rules_fixed_not_negative')),
    sa.CheckConstraint('pct_bps >= 0', name=op.f('ck_settlement_rate_rules_pct_not_negative')),
    sa.ForeignKeyConstraint(['channel_id'], ['sales_channels.id'], name=op.f('fk_settlement_rate_rules_channel_id_sales_channels')),
    sa.ForeignKeyConstraint(['payer_partner_id'], ['partners.id'], name=op.f('fk_settlement_rate_rules_payer_partner_id_partners')),
    sa.ForeignKeyConstraint(['rate_card_id'], ['rate_cards.id'], name=op.f('fk_settlement_rate_rules_rate_card_id_rate_cards'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_settlement_rate_rules')),
    sa.UniqueConstraint('rate_card_id', 'channel_id', 'payment_method', 'event', name='uq_settlement_rate_rules_dimensions')
    )
    op.create_table('tax_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('gst_tcs', 'income_tax_tds', name='tax_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('rate_bps', sa.Integer(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['sales_channels.id'], name=op.f('fk_tax_rules_channel_id_sales_channels')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tax_rules')),
    sa.UniqueConstraint('kind', 'channel_id', 'effective_from', name=op.f('uq_tax_rules_kind_channel_id_effective_from'))
    )
    op.create_index('ix_tax_rules_lookup', 'tax_rules', ['channel_id', 'kind', 'effective_from'], unique=False)
    op.create_table('uploaded_file_blobs',
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('content_gzip', sa.LargeBinary(), nullable=False),
    sa.ForeignKeyConstraint(['file_id'], ['uploaded_files.id'], name=op.f('fk_uploaded_file_blobs_file_id_uploaded_files'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('file_id', name=op.f('pk_uploaded_file_blobs'))
    )
    op.create_table('cod_remittances',
    sa.Column('dedupe_key', sa.String(length=128), nullable=False),
    sa.Column('remittance_date', sa.Date(), nullable=False),
    sa.Column('net_remittance', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('cod_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('cod_fee', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('remittance_ref', sa.String(length=128), nullable=True),
    sa.Column('utr', sa.String(length=64), nullable=True),
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_row_number', sa.Integer(), nullable=False),
    sa.Column('tracking_id', sa.String(length=64), nullable=True),
    sa.Column('external_order_ref', sa.String(length=64), nullable=True),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('match_method', sa.Enum('exact_order_id', 'normalised_order_id', 'tracking_id', 'partner_ref', 'human_review', name='cod_remittances_match_method', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_cod_remittances_order_id_orders')),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_cod_remittances_partner_id_partners')),
    sa.ForeignKeyConstraint(['source_file_id'], ['uploaded_files.id'], name=op.f('fk_cod_remittances_source_file_id_uploaded_files')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cod_remittances')),
    sa.UniqueConstraint('partner_id', 'dedupe_key', name=op.f('uq_cod_remittances_partner_id_dedupe_key'))
    )
    op.create_index('ix_cod_remittances_external_order_ref', 'cod_remittances', ['external_order_ref'], unique=False)
    op.create_index(op.f('ix_cod_remittances_order_id'), 'cod_remittances', ['order_id'], unique=False)
    op.create_index(op.f('ix_cod_remittances_source_file_id'), 'cod_remittances', ['source_file_id'], unique=False)
    op.create_index('ix_cod_remittances_tracking_id', 'cod_remittances', ['tracking_id'], unique=False)
    op.create_table('discrepancies',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('category', sa.Enum('short_paid', 'unpaid_past_due', 'freight_overcharged', 'return_not_received', 'needs_review', name='discrepancy_category', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('counterparty_partner_id', sa.Integer(), nullable=False),
    sa.Column('expected', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('actual', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('difference', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('state', sa.Enum('open', 'resolved', 'superseded', name='discrepancy_state', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('first_seen_run_id', sa.Integer(), nullable=False),
    sa.Column('last_seen_run_id', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['counterparty_partner_id'], ['partners.id'], name=op.f('fk_discrepancies_counterparty_partner_id_partners')),
    sa.ForeignKeyConstraint(['first_seen_run_id'], ['reconciliation_runs.id'], name=op.f('fk_discrepancies_first_seen_run_id_reconciliation_runs')),
    sa.ForeignKeyConstraint(['last_seen_run_id'], ['reconciliation_runs.id'], name=op.f('fk_discrepancies_last_seen_run_id_reconciliation_runs')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_discrepancies_order_id_orders')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_discrepancies')),
    sa.UniqueConstraint('order_id', 'category', 'counterparty_partner_id', name=op.f('uq_discrepancies_order_id_category_counterparty_partner_id'))
    )
    op.create_index('ix_discrepancies_open_due', 'discrepancies', ['due_date'], unique=False, postgresql_where=sa.text("state = 'open'"))
    op.create_index('ix_discrepancies_open_partner', 'discrepancies', ['counterparty_partner_id'], unique=False, postgresql_where=sa.text("state = 'open'"))
    op.create_table('freight_invoice_lines',
    sa.Column('invoice_no', sa.String(length=64), nullable=False),
    sa.Column('invoice_date', sa.Date(), nullable=False),
    sa.Column('total_charge', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('shipment_type', sa.Enum('forward', 'rto', 'customer_return', name='freight_line_shipment_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('billed_weight_grams', sa.Integer(), nullable=True),
    sa.Column('weight_slab', sa.String(length=32), nullable=True),
    sa.Column('zone', sa.String(length=32), nullable=True),
    sa.Column('base_freight', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('cod_charge', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('other_charges', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('gst_on_freight', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_row_number', sa.Integer(), nullable=False),
    sa.Column('tracking_id', sa.String(length=64), nullable=True),
    sa.Column('external_order_ref', sa.String(length=64), nullable=True),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('match_method', sa.Enum('exact_order_id', 'normalised_order_id', 'tracking_id', 'partner_ref', 'human_review', name='freight_invoice_lines_match_method', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_freight_invoice_lines_order_id_orders')),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_freight_invoice_lines_partner_id_partners')),
    sa.ForeignKeyConstraint(['source_file_id'], ['uploaded_files.id'], name=op.f('fk_freight_invoice_lines_source_file_id_uploaded_files')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_freight_invoice_lines')),
    sa.UniqueConstraint('partner_id', 'invoice_no', 'tracking_id', 'shipment_type', name='uq_freight_invoice_lines_natural_key')
    )
    op.create_index('ix_freight_invoice_lines_external_order_ref', 'freight_invoice_lines', ['external_order_ref'], unique=False)
    op.create_index(op.f('ix_freight_invoice_lines_order_id'), 'freight_invoice_lines', ['order_id'], unique=False)
    op.create_index(op.f('ix_freight_invoice_lines_source_file_id'), 'freight_invoice_lines', ['source_file_id'], unique=False)
    op.create_index('ix_freight_invoice_lines_tracking_id', 'freight_invoice_lines', ['tracking_id'], unique=False)
    op.create_table('import_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('file_id', sa.Integer(), nullable=False),
    sa.Column('mapping_version_id', sa.Integer(), nullable=False),
    sa.Column('background_job_id', sa.Integer(), nullable=False),
    sa.Column('rows_total', sa.Integer(), nullable=True),
    sa.Column('rows_inserted', sa.Integer(), nullable=True),
    sa.Column('rows_updated', sa.Integer(), nullable=True),
    sa.Column('rows_duplicate', sa.Integer(), nullable=True),
    sa.Column('rows_rejected', sa.Integer(), nullable=True),
    sa.Column('timings_ms', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['background_job_id'], ['background_jobs.id'], name=op.f('fk_import_jobs_background_job_id_background_jobs')),
    sa.ForeignKeyConstraint(['file_id'], ['uploaded_files.id'], name=op.f('fk_import_jobs_file_id_uploaded_files')),
    sa.ForeignKeyConstraint(['mapping_version_id'], ['mapping_versions.id'], name=op.f('fk_import_jobs_mapping_version_id_mapping_versions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_jobs')),
    sa.UniqueConstraint('background_job_id', name=op.f('uq_import_jobs_background_job_id'))
    )
    op.create_table('match_suggestions',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_table', sa.String(length=64), nullable=False),
    sa.Column('source_id', sa.BigInteger(), nullable=False),
    sa.Column('candidate_order_id', sa.BigInteger(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('method', sa.String(length=32), nullable=False),
    sa.Column('state', sa.Enum('pending', 'confirmed', 'rejected', name='suggestion_state', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_order_id'], ['orders.id'], name=op.f('fk_match_suggestions_candidate_order_id_orders')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_match_suggestions_reviewed_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_suggestions')),
    sa.UniqueConstraint('source_table', 'source_id', 'candidate_order_id', name=op.f('uq_match_suggestions_source_table_source_id_candidate_order_id'))
    )
    op.create_index('ix_match_suggestions_pending', 'match_suggestions', ['created_at'], unique=False, postgresql_where=sa.text("state = 'pending'"))
    op.create_table('reconciliation_results',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('primary_status', sa.Enum('settled', 'short_paid', 'unpaid_past_due', 'freight_overcharged', 'return_not_received', 'needs_review', 'pending', name='recon_result_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('expected_paise', sa.BigInteger(), nullable=True),
    sa.Column('actual_paise', sa.BigInteger(), nullable=True),
    sa.Column('tcs_paise', sa.BigInteger(), nullable=True),
    sa.Column('tds_paise', sa.BigInteger(), nullable=True),
    sa.Column('explanation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_reconciliation_results_order_id_orders')),
    sa.ForeignKeyConstraint(['run_id'], ['reconciliation_runs.id'], name=op.f('fk_reconciliation_results_run_id_reconciliation_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reconciliation_results')),
    sa.UniqueConstraint('run_id', 'order_id', name=op.f('uq_reconciliation_results_run_id_order_id'))
    )
    op.create_index('ix_reconciliation_results_current_status', 'reconciliation_results', ['primary_status'], unique=False, postgresql_where=sa.text('is_current'))
    op.create_index('uq_reconciliation_results_current_order', 'reconciliation_results', ['order_id'], unique=True, postgresql_where=sa.text('is_current'))
    op.create_table('return_records',
    sa.Column('dedupe_key', sa.String(length=128), nullable=False),
    sa.Column('return_type', sa.String(length=32), nullable=False),
    sa.Column('returned_at', sa.Date(), nullable=False),
    sa.Column('return_status', sa.String(length=32), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('return_value', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_row_number', sa.Integer(), nullable=False),
    sa.Column('tracking_id', sa.String(length=64), nullable=True),
    sa.Column('external_order_ref', sa.String(length=64), nullable=True),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('match_method', sa.Enum('exact_order_id', 'normalised_order_id', 'tracking_id', 'partner_ref', 'human_review', name='return_records_match_method', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_return_records_order_id_orders')),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_return_records_partner_id_partners')),
    sa.ForeignKeyConstraint(['source_file_id'], ['uploaded_files.id'], name=op.f('fk_return_records_source_file_id_uploaded_files')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_return_records')),
    sa.UniqueConstraint('partner_id', 'dedupe_key', name=op.f('uq_return_records_partner_id_dedupe_key'))
    )
    op.create_index('ix_return_records_external_order_ref', 'return_records', ['external_order_ref'], unique=False)
    op.create_index(op.f('ix_return_records_order_id'), 'return_records', ['order_id'], unique=False)
    op.create_index(op.f('ix_return_records_source_file_id'), 'return_records', ['source_file_id'], unique=False)
    op.create_index('ix_return_records_tracking_id', 'return_records', ['tracking_id'], unique=False)
    op.create_table('settlement_lines',
    sa.Column('dedupe_key', sa.String(length=128), nullable=False),
    sa.Column('settlement_ref', sa.String(length=128), nullable=False),
    sa.Column('settlement_date', sa.Date(), nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('commission', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('fees', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('tcs_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('tds_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('transaction_type', sa.String(length=32), nullable=True),
    sa.Column('utr', sa.String(length=64), nullable=True),
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_row_number', sa.Integer(), nullable=False),
    sa.Column('tracking_id', sa.String(length=64), nullable=True),
    sa.Column('external_order_ref', sa.String(length=64), nullable=True),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('match_method', sa.Enum('exact_order_id', 'normalised_order_id', 'tracking_id', 'partner_ref', 'human_review', name='settlement_lines_match_method', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_settlement_lines_order_id_orders')),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_settlement_lines_partner_id_partners')),
    sa.ForeignKeyConstraint(['source_file_id'], ['uploaded_files.id'], name=op.f('fk_settlement_lines_source_file_id_uploaded_files')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_settlement_lines')),
    sa.UniqueConstraint('partner_id', 'dedupe_key', name=op.f('uq_settlement_lines_partner_id_dedupe_key'))
    )
    op.create_index('ix_settlement_lines_external_order_ref', 'settlement_lines', ['external_order_ref'], unique=False)
    op.create_index(op.f('ix_settlement_lines_order_id'), 'settlement_lines', ['order_id'], unique=False)
    op.create_index(op.f('ix_settlement_lines_source_file_id'), 'settlement_lines', ['source_file_id'], unique=False)
    op.create_index('ix_settlement_lines_tracking_id', 'settlement_lines', ['tracking_id'], unique=False)
    op.create_table('warehouse_receipts',
    sa.Column('receipt_ref', sa.String(length=128), nullable=False),
    sa.Column('received_at', sa.Date(), nullable=False),
    sa.Column('quantity_received', sa.Integer(), nullable=True),
    sa.Column('condition', sa.String(length=32), nullable=True),
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_row_number', sa.Integer(), nullable=False),
    sa.Column('tracking_id', sa.String(length=64), nullable=True),
    sa.Column('external_order_ref', sa.String(length=64), nullable=True),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('source_file_id', sa.Integer(), nullable=False),
    sa.Column('match_method', sa.Enum('exact_order_id', 'normalised_order_id', 'tracking_id', 'partner_ref', 'human_review', name='warehouse_receipts_match_method', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_warehouse_receipts_order_id_orders')),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_warehouse_receipts_partner_id_partners')),
    sa.ForeignKeyConstraint(['source_file_id'], ['uploaded_files.id'], name=op.f('fk_warehouse_receipts_source_file_id_uploaded_files')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_warehouse_receipts')),
    sa.UniqueConstraint('receipt_ref', name=op.f('uq_warehouse_receipts_receipt_ref'))
    )
    op.create_index('ix_warehouse_receipts_external_order_ref', 'warehouse_receipts', ['external_order_ref'], unique=False)
    op.create_index(op.f('ix_warehouse_receipts_order_id'), 'warehouse_receipts', ['order_id'], unique=False)
    op.create_index(op.f('ix_warehouse_receipts_source_file_id'), 'warehouse_receipts', ['source_file_id'], unique=False)
    op.create_index('ix_warehouse_receipts_tracking_id', 'warehouse_receipts', ['tracking_id'], unique=False)
    op.create_table('claims',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('claim_no', sa.String(length=32), nullable=False),
    sa.Column('discrepancy_id', sa.BigInteger(), nullable=False),
    sa.Column('partner_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('category', sa.Enum('short_paid', 'unpaid_past_due', 'freight_overcharged', 'return_not_received', 'needs_review', name='claim_category', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('expected', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('actual', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('difference', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('recovered_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.Enum('draft', 'submitted', 'acknowledged', 'partially_recovered', 'recovered', 'rejected', 'written_off', name='claim_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('resolution_notes', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('recovered_amount >= 0', name=op.f('ck_claims_recovered_not_negative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_claims_created_by_users')),
    sa.ForeignKeyConstraint(['discrepancy_id'], ['discrepancies.id'], name=op.f('fk_claims_discrepancy_id_discrepancies')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_claims_order_id_orders')),
    sa.ForeignKeyConstraint(['partner_id'], ['partners.id'], name=op.f('fk_claims_partner_id_partners')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_claims')),
    sa.UniqueConstraint('claim_no', name=op.f('uq_claims_claim_no'))
    )
    op.create_index('ix_claims_status', 'claims', ['status'], unique=False)
    op.create_index('uq_claims_active_discrepancy', 'claims', ['discrepancy_id'], unique=True, postgresql_where=sa.text("status NOT IN ('rejected', 'written_off')"))
    op.create_table('import_row_errors',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('import_job_id', sa.Integer(), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(length=64), nullable=True),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('raw_value', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['import_job_id'], ['import_jobs.id'], name=op.f('fk_import_row_errors_import_job_id_import_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_row_errors'))
    )
    op.create_index('ix_import_row_errors_job_row', 'import_row_errors', ['import_job_id', 'row_number'], unique=False)
    op.create_table('claim_events',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('claim_id', sa.BigInteger(), nullable=False),
    sa.Column('from_status', sa.Enum('draft', 'submitted', 'acknowledged', 'partially_recovered', 'recovered', 'rejected', 'written_off', name='claim_event_from_status', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('to_status', sa.Enum('draft', 'submitted', 'acknowledged', 'partially_recovered', 'recovered', 'rejected', 'written_off', name='claim_event_to_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_claim_events_actor_id_users')),
    sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], name=op.f('fk_claim_events_claim_id_claims'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_claim_events'))
    )
    op.create_index('ix_claim_events_claim', 'claim_events', ['claim_id', 'created_at'], unique=False)

    # Audit rows can't be altered, even by code with a bug or by a direct SQL session.
    op.execute(
        """
        CREATE FUNCTION reject_audit_log_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION reject_audit_log_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION reject_audit_log_change()")
    op.drop_constraint(
        'fk_column_mappings_active_version', 'column_mappings', type_='foreignkey'
    )
    op.drop_index('ix_claim_events_claim', table_name='claim_events')
    op.drop_table('claim_events')
    op.drop_index('ix_import_row_errors_job_row', table_name='import_row_errors')
    op.drop_table('import_row_errors')
    op.drop_index('uq_claims_active_discrepancy', table_name='claims', postgresql_where=sa.text("status NOT IN ('rejected', 'written_off')"))
    op.drop_index('ix_claims_status', table_name='claims')
    op.drop_table('claims')
    op.drop_index('ix_warehouse_receipts_tracking_id', table_name='warehouse_receipts')
    op.drop_index(op.f('ix_warehouse_receipts_source_file_id'), table_name='warehouse_receipts')
    op.drop_index(op.f('ix_warehouse_receipts_order_id'), table_name='warehouse_receipts')
    op.drop_index('ix_warehouse_receipts_external_order_ref', table_name='warehouse_receipts')
    op.drop_table('warehouse_receipts')
    op.drop_index('ix_settlement_lines_tracking_id', table_name='settlement_lines')
    op.drop_index(op.f('ix_settlement_lines_source_file_id'), table_name='settlement_lines')
    op.drop_index(op.f('ix_settlement_lines_order_id'), table_name='settlement_lines')
    op.drop_index('ix_settlement_lines_external_order_ref', table_name='settlement_lines')
    op.drop_table('settlement_lines')
    op.drop_index('ix_return_records_tracking_id', table_name='return_records')
    op.drop_index(op.f('ix_return_records_source_file_id'), table_name='return_records')
    op.drop_index(op.f('ix_return_records_order_id'), table_name='return_records')
    op.drop_index('ix_return_records_external_order_ref', table_name='return_records')
    op.drop_table('return_records')
    op.drop_index('uq_reconciliation_results_current_order', table_name='reconciliation_results', postgresql_where=sa.text('is_current'))
    op.drop_index('ix_reconciliation_results_current_status', table_name='reconciliation_results', postgresql_where=sa.text('is_current'))
    op.drop_table('reconciliation_results')
    op.drop_index('ix_match_suggestions_pending', table_name='match_suggestions', postgresql_where=sa.text("state = 'pending'"))
    op.drop_table('match_suggestions')
    op.drop_table('import_jobs')
    op.drop_index('ix_freight_invoice_lines_tracking_id', table_name='freight_invoice_lines')
    op.drop_index(op.f('ix_freight_invoice_lines_source_file_id'), table_name='freight_invoice_lines')
    op.drop_index(op.f('ix_freight_invoice_lines_order_id'), table_name='freight_invoice_lines')
    op.drop_index('ix_freight_invoice_lines_external_order_ref', table_name='freight_invoice_lines')
    op.drop_table('freight_invoice_lines')
    op.drop_index('ix_discrepancies_open_partner', table_name='discrepancies', postgresql_where=sa.text("state = 'open'"))
    op.drop_index('ix_discrepancies_open_due', table_name='discrepancies', postgresql_where=sa.text("state = 'open'"))
    op.drop_table('discrepancies')
    op.drop_index('ix_cod_remittances_tracking_id', table_name='cod_remittances')
    op.drop_index(op.f('ix_cod_remittances_source_file_id'), table_name='cod_remittances')
    op.drop_index(op.f('ix_cod_remittances_order_id'), table_name='cod_remittances')
    op.drop_index('ix_cod_remittances_external_order_ref', table_name='cod_remittances')
    op.drop_table('cod_remittances')
    op.drop_table('uploaded_file_blobs')
    op.drop_index('ix_tax_rules_lookup', table_name='tax_rules')
    op.drop_table('tax_rules')
    op.drop_table('settlement_rate_rules')
    op.drop_table('reconciliation_runs')
    op.drop_index('ix_orders_tracking_id', table_name='orders')
    op.drop_index('ix_orders_status_date', table_name='orders')
    op.drop_table('orders')
    op.drop_table('mapping_versions')
    op.drop_table('uploaded_files')
    op.drop_table('sales_channels')
    op.drop_table('freight_rate_rules')
    op.drop_table('column_mappings')
    op.drop_index('ix_background_jobs_claimable', table_name='background_jobs', postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.drop_table('background_jobs')
    op.drop_index('ix_audit_logs_entity', table_name='audit_logs')
    op.drop_index('ix_audit_logs_actor_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('users')
    op.drop_table('settings')
    op.drop_table('rate_cards')
    op.drop_table('partners')
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
