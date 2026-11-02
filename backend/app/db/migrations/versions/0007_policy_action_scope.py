from alembic import op
import sqlalchemy as sa


revision = "0007_policy_action_scope"
down_revision = "0006_drop_legacy_idempotency_index"
branch_labels = None
depends_on = None


def upgrade():
    policy_columns = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("policies")}
    if "action_type" not in policy_columns:
        with op.batch_alter_table("policies") as batch:
            batch.add_column(sa.Column("action_type", sa.String(), nullable=True))
    op.execute(sa.text("UPDATE policies SET action_type = 'quote_discount' WHERE action_type IS NULL"))
    if policy_columns.get("action_type", {}).get("nullable", True):
        with op.batch_alter_table("policies") as batch:
            batch.alter_column(
                "action_type", existing_type=sa.String(), nullable=False,
                server_default="quote_discount",
            )
    discount_column = next(column for column in sa.inspect(op.get_bind()).get_columns("policies") if column["name"] == "max_discount_pct")
    if not discount_column["nullable"]:
        with op.batch_alter_table("policies") as batch:
            batch.alter_column("max_discount_pct", existing_type=sa.Numeric(5, 2), nullable=True)

    draft_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("policy_drafts")}
    if "action_type" not in draft_columns:
        with op.batch_alter_table("policy_drafts") as batch:
            batch.add_column(sa.Column("action_type", sa.String(), nullable=True))


def downgrade():
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("policies")}
    if "action_type" in columns and connection.execute(
        sa.text("SELECT 1 FROM policies WHERE action_type = 'reference_only' LIMIT 1")
    ).first():
        raise RuntimeError("Cannot downgrade while reference-only policies exist.")
    discount_column = next(column for column in sa.inspect(connection).get_columns("policies") if column["name"] == "max_discount_pct")
    if discount_column["nullable"]:
        with op.batch_alter_table("policies") as batch:
            batch.alter_column("max_discount_pct", existing_type=sa.Numeric(5, 2), nullable=False, server_default=None)
    for table in ("policy_drafts", "policies"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "action_type" in columns:
            with op.batch_alter_table(table) as batch:
                batch.drop_column("action_type")
