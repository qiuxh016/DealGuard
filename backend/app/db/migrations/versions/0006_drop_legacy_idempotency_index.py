from alembic import op
import sqlalchemy as sa


revision = "0006_drop_legacy_idempotency_index"
down_revision = "0005_auth_tenant"
branch_labels = None
depends_on = None


def upgrade():
    indexes = sa.inspect(op.get_bind()).get_indexes("runs")
    if any(item["name"] == "uq_runs_actor_idempotency_key" for item in indexes):
        op.drop_index("uq_runs_actor_idempotency_key", table_name="runs")


def downgrade():
    # The fixed 0005 schema intentionally has only tenant-scoped uniqueness.
    pass
