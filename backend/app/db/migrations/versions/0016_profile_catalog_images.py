from alembic import op
import sqlalchemy as sa


revision = "0016_profile_catalog_images"
down_revision = "0015_policy_version_uniqueness"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "media_assets" not in inspector.get_table_names():
        op.create_table(
            "media_assets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("uploaded_by", sa.String(), nullable=False),
            sa.Column("content_type", sa.String(length=32), nullable=False),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_media_assets_tenant_id", "media_assets", ["tenant_id"])

    for table_name in ("customers", "products", "demo_actors"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
        if "image_asset_id" in columns:
            continue
        op.add_column(table_name, sa.Column("image_asset_id", sa.String(), nullable=True))


def downgrade():
    for table_name in ("demo_actors", "products", "customers"):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
        if "image_asset_id" not in columns:
            continue
        op.drop_column(table_name, "image_asset_id")
    if "media_assets" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_media_assets_tenant_id", table_name="media_assets")
        op.drop_table("media_assets")
