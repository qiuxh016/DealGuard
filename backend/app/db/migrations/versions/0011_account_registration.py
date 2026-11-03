from alembic import op
import sqlalchemy as sa


revision = "0011_account_registration"
down_revision = "0010_policy_applicability_scope"
branch_labels = None
depends_on = None


def upgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("auth_accounts")}
    if "email" not in columns:
        op.add_column("auth_accounts", sa.Column("email", sa.String(), nullable=True))
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("auth_accounts")}
    if "ix_auth_accounts_email" not in indexes:
        op.create_index("ix_auth_accounts_email", "auth_accounts", ["email"], unique=True)


def downgrade():
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("auth_accounts")}
    if "ix_auth_accounts_email" in indexes:
        op.drop_index("ix_auth_accounts_email", table_name="auth_accounts")
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("auth_accounts")}
    if "email" in columns:
        op.drop_column("auth_accounts", "email")
