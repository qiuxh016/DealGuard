from alembic import op
import sqlalchemy as sa


revision = "0012_workspace_invitations"
down_revision = "0011_account_registration"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "workspace_invitations" not in inspector.get_table_names():
        op.create_table(
            "workspace_invitations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("workspace_invitations")}
    if "ix_workspace_invitations_tenant_id" not in indexes:
        op.create_index("ix_workspace_invitations_tenant_id", "workspace_invitations", ["tenant_id"])
    if "ix_workspace_invitations_email" not in indexes:
        op.create_index("ix_workspace_invitations_email", "workspace_invitations", ["email"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "workspace_invitations" not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes("workspace_invitations")}
    if "ix_workspace_invitations_email" in indexes:
        op.drop_index("ix_workspace_invitations_email", table_name="workspace_invitations")
    if "ix_workspace_invitations_tenant_id" in indexes:
        op.drop_index("ix_workspace_invitations_tenant_id", table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
