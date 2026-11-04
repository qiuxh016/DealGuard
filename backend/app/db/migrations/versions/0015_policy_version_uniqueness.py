from alembic import op
import sqlalchemy as sa
from uuid import uuid4


revision = "0015_policy_version_uniqueness"
down_revision = "0014_product_sku"
branch_labels = None
depends_on = None


def _duplicate(bind, table):
    return bind.execute(sa.text(
        f"SELECT tenant_id, policy_id, version FROM {table} "
        "GROUP BY tenant_id, policy_id, version HAVING COUNT(*) > 1 LIMIT 1"
    )).first()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("policies", "policy_drafts"):
        duplicate = _duplicate(bind, table)
        if duplicate:
            raise RuntimeError(
                f"Cannot add policy version uniqueness: {table} contains duplicate "
                f"tenant={duplicate.tenant_id!r}, policy_id={duplicate.policy_id!r}, version={duplicate.version!r}. "
                "Resolve duplicates before retrying the migration."
            )

    policies = sa.Table("policies", sa.MetaData(), autoload_with=bind)
    drafts = sa.Table("policy_drafts", sa.MetaData(), autoload_with=bind)
    policy_rows = bind.execute(sa.select(policies.c.id, policies.c.tenant_id, policies.c.policy_id, policies.c.version)).mappings().all()
    policy_by_key = {(row["tenant_id"], row["policy_id"], row["version"]): row["id"] for row in policy_rows}
    draft_rows = bind.execute(sa.select(drafts.c.tenant_id, drafts.c.policy_id, drafts.c.version, drafts.c.policy_row_id)).mappings().all()
    for row in draft_rows:
        key = (row["tenant_id"], row["policy_id"], row["version"])
        policy_id = policy_by_key.get(key)
        if policy_id and row["policy_row_id"] != policy_id:
            raise RuntimeError(
                f"Cannot add policy version uniqueness: a policy and an unrelated draft share "
                f"tenant={key[0]!r}, policy_id={key[1]!r}, version={key[2]!r}. Resolve the conflict first."
            )

    if "uq_policy_tenant_version" not in {item["name"] for item in inspector.get_unique_constraints("policies")}:
        with op.batch_alter_table("policies") as batch:
            batch.create_unique_constraint("uq_policy_tenant_version", ["tenant_id", "policy_id", "version"])
    if "uq_policy_draft_tenant_version" not in {item["name"] for item in inspector.get_unique_constraints("policy_drafts")}:
        with op.batch_alter_table("policy_drafts") as batch:
            batch.create_unique_constraint("uq_policy_draft_tenant_version", ["tenant_id", "policy_id", "version"])
    if "policy_version_keys" not in inspector.get_table_names():
        op.create_table(
            "policy_version_keys",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("policy_id", sa.String(), nullable=False),
            sa.Column("version", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("id", name="pk_policy_version_keys"),
            sa.UniqueConstraint("tenant_id", "policy_id", "version", name="uq_policy_version_key"),
        )
    for row in policy_rows:
        exists = bind.execute(sa.text(
            "SELECT 1 FROM policy_version_keys WHERE tenant_id=:tenant_id AND policy_id=:policy_id AND version=:version"
        ), {"tenant_id": row["tenant_id"], "policy_id": row["policy_id"], "version": row["version"]}).first()
        if not exists:
            bind.execute(sa.text(
                "INSERT INTO policy_version_keys (id, tenant_id, policy_id, version) "
                "VALUES (:id, :tenant_id, :policy_id, :version)"
            ), {"id": str(uuid4()), "tenant_id": row["tenant_id"], "policy_id": row["policy_id"], "version": row["version"]})
    for row in draft_rows:
        key = (row["tenant_id"], row["policy_id"], row["version"])
        if key in policy_by_key:
            continue
        exists = bind.execute(sa.text(
            "SELECT 1 FROM policy_version_keys WHERE tenant_id=:tenant_id AND policy_id=:policy_id AND version=:version"
        ), {"tenant_id": key[0], "policy_id": key[1], "version": key[2]}).first()
        if exists:
            continue
        bind.execute(sa.text(
            "INSERT INTO policy_version_keys (id, tenant_id, policy_id, version) "
            "VALUES (:id, :tenant_id, :policy_id, :version)"
        ), {"id": str(uuid4()), "tenant_id": row["tenant_id"], "policy_id": row["policy_id"], "version": row["version"]})


def downgrade():
    op.drop_table("policy_version_keys")
    with op.batch_alter_table("policy_drafts") as batch:
        batch.drop_constraint("uq_policy_draft_tenant_version", type_="unique")
    with op.batch_alter_table("policies") as batch:
        batch.drop_constraint("uq_policy_tenant_version", type_="unique")
