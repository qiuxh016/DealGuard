from alembic import op
import sqlalchemy as sa


revision = "0013_password_reset_tokens"
down_revision = "0012_workspace_invitations"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "password_reset_tokens" not in inspector.get_table_names():
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("actor_id", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("requested_by", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("password_reset_tokens")}
    if "ix_password_reset_tokens_actor_id" not in indexes:
        op.create_index("ix_password_reset_tokens_actor_id", "password_reset_tokens", ["actor_id"])
    if "ix_password_reset_tokens_tenant_id" not in indexes:
        op.create_index("ix_password_reset_tokens_tenant_id", "password_reset_tokens", ["tenant_id"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "password_reset_tokens" not in inspector.get_table_names():
        return
    indexes = {item["name"] for item in inspector.get_indexes("password_reset_tokens")}
    if "ix_password_reset_tokens_tenant_id" in indexes:
        op.drop_index("ix_password_reset_tokens_tenant_id", table_name="password_reset_tokens")
    if "ix_password_reset_tokens_actor_id" in indexes:
        op.drop_index("ix_password_reset_tokens_actor_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
