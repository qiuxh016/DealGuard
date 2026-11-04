from alembic import op
import sqlalchemy as sa


revision = "0017_quote_lifecycle"
down_revision = "0016_profile_catalog_images"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "quote_revisions" not in tables:
        op.create_table(
        "quote_revisions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("quote_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quote_drafts.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["demo_actors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "quote_id", "version", name="uq_quote_revision_tenant_version"),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("quote_revisions")}
    if "ix_quote_revisions_tenant_id" not in indexes:
        op.create_index("ix_quote_revisions_tenant_id", "quote_revisions", ["tenant_id"])
    if "ix_quote_revisions_quote_id" not in indexes:
        op.create_index("ix_quote_revisions_quote_id", "quote_revisions", ["quote_id"])

    if "quote_status_events" not in tables:
        op.create_table(
        "quote_status_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("quote_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), nullable=True),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quote_drafts.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["quote_revisions.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["demo_actors.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "quote_id", "idempotency_key", name="uq_quote_event_idempotency"),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("quote_status_events")}
    if "ix_quote_status_events_tenant_id" not in indexes:
        op.create_index("ix_quote_status_events_tenant_id", "quote_status_events", ["tenant_id"])
    if "ix_quote_status_events_quote_id" not in indexes:
        op.create_index("ix_quote_status_events_quote_id", "quote_status_events", ["quote_id"])

    if "quote_access_tokens" not in tables:
        op.create_table(
        "quote_access_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("revision_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["quote_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("quote_access_tokens")}
    if "ix_quote_access_tokens_tenant_id" not in indexes:
        op.create_index("ix_quote_access_tokens_tenant_id", "quote_access_tokens", ["tenant_id"])
    if "ix_quote_access_tokens_revision_id" not in indexes:
        op.create_index("ix_quote_access_tokens_revision_id", "quote_access_tokens", ["revision_id"])


def downgrade():
    op.drop_index("ix_quote_access_tokens_revision_id", table_name="quote_access_tokens")
    op.drop_index("ix_quote_access_tokens_tenant_id", table_name="quote_access_tokens")
    op.drop_table("quote_access_tokens")
    op.drop_index("ix_quote_status_events_quote_id", table_name="quote_status_events")
    op.drop_index("ix_quote_status_events_tenant_id", table_name="quote_status_events")
    op.drop_table("quote_status_events")
    op.drop_index("ix_quote_revisions_quote_id", table_name="quote_revisions")
    op.drop_index("ix_quote_revisions_tenant_id", table_name="quote_revisions")
    op.drop_table("quote_revisions")
