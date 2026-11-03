from alembic import op
import sqlalchemy as sa

revision = "0009_multi_item_quotes"
down_revision = "0008_policy_audit"
branch_labels = None
depends_on = None

def upgrade():
    for table in ("quote_drafts", "approval_requests"):
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        if "lines" not in columns:
            op.add_column(table, sa.Column("lines", sa.JSON(), nullable=True))

def downgrade():
    for table in ("approval_requests", "quote_drafts"):
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        if "lines" in columns:
            op.drop_column(table, "lines")
