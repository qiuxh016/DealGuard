from alembic import op
import sqlalchemy as sa


revision = "0004_run_recovery"
down_revision = "0003_run_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("runs")}
    if "attempt" not in columns:
        op.add_column("runs", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    if "lease_expires_at" not in columns:
        op.add_column("runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("runs", "lease_expires_at")
    op.drop_column("runs", "attempt")
