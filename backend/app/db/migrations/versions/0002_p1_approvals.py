from alembic import op
import sqlalchemy as sa

revision = "0002_p1_approvals"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "policies" in inspector.get_table_names() and "review_max_discount_pct" not in {c["name"] for c in inspector.get_columns("policies")}:
        op.add_column("policies", sa.Column("review_max_discount_pct", sa.Numeric(5, 2), nullable=True))
    if "runs" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("runs")}
        for name in ("duration_ms", "input_tokens", "output_tokens"):
            if name not in existing:
                op.add_column("runs", sa.Column(name, sa.Integer(), nullable=True))
    tables = set(sa.inspect(bind).get_table_names())
    if "approval_requests" not in tables:
        op.create_table("approval_requests",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
            sa.Column("applicant_actor_id", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
            sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False),
            sa.Column("policy_id", sa.String(), nullable=False),
            sa.Column("policy_version", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False, server_default="PENDING"),
            sa.Column("reason", sa.Text()),
            sa.Column("resolved_by", sa.String(), sa.ForeignKey("demo_actors.id")),
            sa.Column("resolved_at", sa.DateTime(timezone=True)),
            sa.Column("quote_id", sa.String(), sa.ForeignKey("quote_drafts.id")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_approval_requests_run_id", "approval_requests", ["run_id"])
    if "policy_drafts" not in tables:
        op.create_table("policy_drafts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("policy_id", sa.String(), nullable=False),
            sa.Column("version", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("customer_tier", sa.String(), nullable=False),
            sa.Column("max_discount_pct", sa.Numeric(5, 2)),
            sa.Column("review_max_discount_pct", sa.Numeric(5, 2)),
            sa.Column("exception_approval_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="draft"),
            sa.Column("uploaded_by", sa.String(), sa.ForeignKey("demo_actors.id"), nullable=False),
            sa.Column("policy_row_id", sa.String(), sa.ForeignKey("policies.id")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("policy_drafts")
    op.drop_index("ix_approval_requests_run_id", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_column("runs", "output_tokens")
    op.drop_column("runs", "input_tokens")
    op.drop_column("runs", "duration_ms")
    op.drop_column("policies", "review_max_discount_pct")
