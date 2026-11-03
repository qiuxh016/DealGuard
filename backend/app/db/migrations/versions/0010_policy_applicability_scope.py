from alembic import op
import sqlalchemy as sa


revision = "0010_policy_applicability_scope"
down_revision = "0009_multi_item_quotes"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("policies", "policy_drafts", "approval_requests", "quote_drafts"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "policy_scope" not in columns:
            op.add_column(table, sa.Column("policy_scope", sa.String(), nullable=False, server_default="general"))
    for table in ("policies", "policy_drafts"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "scope_product_id" not in columns:
            op.add_column(table, sa.Column("scope_product_id", sa.String(), nullable=True))


def downgrade():
    for table in ("policies", "policy_drafts"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "scope_product_id" in columns:
            op.drop_column(table, "scope_product_id")
    for table in ("quote_drafts", "approval_requests", "policy_drafts", "policies"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "policy_scope" in columns:
            op.drop_column(table, "policy_scope")
