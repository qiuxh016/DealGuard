from alembic import op
import sqlalchemy as sa


revision = "0008_policy_audit"
down_revision = "0007_policy_action_scope"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "policy_audit_events" not in inspector.get_table_names():
        op.create_table(
            "policy_audit_events",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("actor_id", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=True),
            sa.Column("policy_id", sa.String(), nullable=True),
            sa.Column("version", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("policy_audit_events")}
    if "ix_policy_audit_events_tenant_id" not in indexes:
        op.create_index("ix_policy_audit_events_tenant_id", "policy_audit_events", ["tenant_id"])
    if "ix_policy_audit_events_actor_id" not in indexes:
        op.create_index("ix_policy_audit_events_actor_id", "policy_audit_events", ["actor_id"])


def downgrade():
    if "policy_audit_events" in sa.inspect(op.get_bind()).get_table_names():
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("policy_audit_events")}
        for name in ("ix_policy_audit_events_actor_id", "ix_policy_audit_events_tenant_id"):
            if name in indexes:
                op.drop_index(name, table_name="policy_audit_events")
        op.drop_table("policy_audit_events")
