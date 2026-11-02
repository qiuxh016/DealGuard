from alembic import op
import sqlalchemy as sa


revision = "0003_run_idempotency"
down_revision = "0002_p1_approvals"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("runs")}
    if "idempotency_key" not in columns:
        op.add_column("runs", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    if "request_hash" not in columns:
        op.add_column("runs", sa.Column("request_hash", sa.String(length=64), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("runs")}
    if "uq_runs_actor_idempotency_key" not in indexes:
        op.create_index(
            "uq_runs_actor_idempotency_key",
            "runs",
            ["actor_id", "idempotency_key"],
            unique=True,
        )


def downgrade():
    op.drop_index("uq_runs_actor_idempotency_key", table_name="runs")
    op.drop_column("runs", "request_hash")
    op.drop_column("runs", "idempotency_key")
