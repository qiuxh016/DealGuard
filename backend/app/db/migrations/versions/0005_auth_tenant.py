from alembic import op
import sqlalchemy as sa


revision = "0005_auth_tenant"
down_revision = "0004_run_recovery"
branch_labels = None
depends_on = None

TENANT_TABLES = ("customers", "demo_actors", "products", "policies", "quote_drafts", "runs", "approval_requests", "policy_drafts")


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "tenants" not in tables:
        op.create_table("tenants", sa.Column("id", sa.String(), primary_key=True), sa.Column("name", sa.String(), nullable=False))
    if not op.get_bind().execute(sa.text("SELECT 1 FROM tenants WHERE id='harborlight-demo'")).first():
        op.execute("INSERT INTO tenants (id, name) VALUES ('harborlight-demo', 'HarborLight Trading')")
    for table in TENANT_TABLES:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "tenant_id" not in columns:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("tenant_id", sa.String(), nullable=True))
            op.execute(sa.text(f"UPDATE {table} SET tenant_id = 'harborlight-demo' WHERE tenant_id IS NULL"))
            with op.batch_alter_table(table) as batch:
                batch.alter_column("tenant_id", existing_type=sa.String(), nullable=False, server_default="harborlight-demo")
                batch.create_foreign_key(f"fk_{table}_tenant_id_tenants", "tenants", ["tenant_id"], ["id"])
    run_indexes = sa.inspect(op.get_bind()).get_indexes("runs")
    if any(item["name"] == "uq_runs_actor_idempotency_key" for item in run_indexes):
        op.drop_index("uq_runs_actor_idempotency_key", table_name="runs")
    run_constraints = sa.inspect(op.get_bind()).get_unique_constraints("runs")
    if any(item["name"] == "uq_runs_actor_idempotency_key" for item in run_constraints):
        with op.batch_alter_table("runs") as batch:
            batch.drop_constraint("uq_runs_actor_idempotency_key", type_="unique")
            batch.create_unique_constraint("uq_runs_tenant_actor_idempotency_key", ["tenant_id", "actor_id", "idempotency_key"])
    elif not any(item["name"] == "uq_runs_tenant_actor_idempotency_key" for item in run_constraints):
        with op.batch_alter_table("runs") as batch:
            batch.create_unique_constraint("uq_runs_tenant_actor_idempotency_key", ["tenant_id", "actor_id", "idempotency_key"])
    if "auth_accounts" not in tables:
        op.create_table(
        "auth_accounts",
        sa.Column("actor_id", sa.String(), sa.ForeignKey("demo_actors.id"), primary_key=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "auth_sessions" not in tables:
        op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_auth_sessions_actor_id", "auth_sessions", ["actor_id"])


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "auth_sessions" in tables:
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("auth_sessions")}
        if "ix_auth_sessions_actor_id" in indexes:
            op.drop_index("ix_auth_sessions_actor_id", table_name="auth_sessions")
        op.drop_table("auth_sessions")
    if "auth_accounts" in tables:
        op.drop_table("auth_accounts")
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("runs")
    indexes = sa.inspect(op.get_bind()).get_indexes("runs")
    if any(item["name"] == "uq_runs_tenant_actor_idempotency_key" and not item.get("duplicates_constraint") for item in indexes):
        op.drop_index("uq_runs_tenant_actor_idempotency_key", table_name="runs")
        op.create_index("uq_runs_actor_idempotency_key", "runs", ["actor_id", "idempotency_key"], unique=True)
    elif any(item["name"] == "uq_runs_tenant_actor_idempotency_key" for item in constraints):
        with op.batch_alter_table("runs") as batch:
            batch.drop_constraint("uq_runs_tenant_actor_idempotency_key", type_="unique")
        op.create_index("uq_runs_actor_idempotency_key", "runs", ["actor_id", "idempotency_key"], unique=True)
    for table in reversed(TENANT_TABLES):
        tenant_foreign_key = next((
            foreign_key for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
            if foreign_key.get("referred_table") == "tenants" and "tenant_id" in foreign_key.get("constrained_columns", [])
        ), None)
        with op.batch_alter_table(table) as batch:
            if tenant_foreign_key and tenant_foreign_key.get("name"):
                batch.drop_constraint(tenant_foreign_key["name"], type_="foreignkey")
            batch.drop_column("tenant_id")
    op.drop_table("tenants")
