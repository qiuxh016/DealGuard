from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "0014_product_sku"
down_revision = "0013_password_reset_tokens"
branch_labels = None
depends_on = None

CHILD_TABLES = ("quote_drafts", "approval_requests")
FK_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _product_fk_name(table_name: str, column_name: str) -> str:
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        if foreign_key["constrained_columns"] == [column_name] and foreign_key["referred_table"] == "products":
            return foreign_key["name"] or f"fk_{table_name}_{column_name}_products"
    raise RuntimeError(f"Expected {table_name}.{column_name} to reference products.")


def _rebuild_child_without_fk(table_name: str, source_column: str, *, drop_product_key: bool = False) -> None:
    fk_name = _product_fk_name(table_name, source_column)
    with op.batch_alter_table(table_name, naming_convention=FK_NAMING) as batch:
        batch.drop_constraint(fk_name, type_="foreignkey")
        if drop_product_key:
            batch.drop_column("product_key")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("PRAGMA defer_foreign_keys=ON")

    product_columns = {column["name"] for column in sa.inspect(bind).get_columns("products")}
    child_columns = {
        table_name: {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        for table_name in CHILD_TABLES
    }
    if "sku" in product_columns and all("product_key" in columns for columns in child_columns.values()):
        return
    if "sku" in product_columns or any("product_key" in columns for columns in child_columns.values()):
        raise RuntimeError("The product SKU migration is only safe from the complete pre-SKU schema.")

    products = sa.Table("products", sa.MetaData(), autoload_with=bind)
    old_rows = bind.execute(sa.select(products)).mappings().all()
    old_skus = [row["sku"] if "sku" in row and row["sku"] else row["id"] for row in old_rows]
    if len(old_skus) != len(set(old_skus)):
        raise RuntimeError("Existing product identifiers are not unique; SKU migration cannot continue safely.")
    id_by_sku = {sku: str(uuid4()) for sku in old_skus}

    for table_name in CHILD_TABLES:
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        if "product_id" not in columns:
            raise RuntimeError(f"{table_name}.product_id is required for a lossless SKU migration.")
        op.add_column(table_name, sa.Column("product_key", sa.String(), nullable=True))
        table = sa.Table(table_name, sa.MetaData(), autoload_with=bind)
        rows = bind.execute(sa.select(table.c.id, table.c.product_id)).all()
        for row in rows:
            product_key = id_by_sku.get(row.product_id)
            if product_key is None:
                raise RuntimeError(f"{table_name} row {row.id} refers to missing product SKU {row.product_id!r}.")
            bind.execute(table.update().where(table.c.id == row.id).values(product_key=product_key))
        _rebuild_child_without_fk(table_name, "product_id")
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column("product_key", existing_type=sa.String(), nullable=False)

    op.create_table(
        "products_uuid",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_products_uuid"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
    )
    product_columns = set(products.c.keys())
    for row, sku in zip(old_rows, old_skus, strict=True):
        tenant_id = row["tenant_id"] if "tenant_id" in product_columns else "harborlight-demo"
        values = {
            "id": id_by_sku[sku],
            "sku": sku,
            "name": row["name"] if "name" in product_columns else sku,
            "currency": row["currency"] if "currency" in product_columns else "USD",
            "unit_price": str(row["unit_price"]) if "unit_price" in product_columns else "0",
            "stock": row["stock"] if "stock" in product_columns else 0,
            "tenant_id": tenant_id,
        }
        bind.execute(sa.text(
            "INSERT INTO products_uuid (id, sku, name, currency, unit_price, stock, tenant_id) "
            "VALUES (:id, :sku, :name, :currency, :unit_price, :stock, :tenant_id)"
        ), values)

    op.drop_table("products")
    op.rename_table("products_uuid", "products")
    for table_name in CHILD_TABLES:
        with op.batch_alter_table(table_name, naming_convention=FK_NAMING) as batch:
            batch.create_foreign_key(
                f"fk_{table_name}_product_key_products", "products", ["product_key"], ["id"],
            )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    duplicate = bind.execute(sa.text(
        "SELECT sku, COUNT(*) AS count FROM products GROUP BY sku HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError(
            f"Cannot downgrade product SKUs: {duplicate.sku!r} exists in multiple workspaces. "
            "Restore a pre-migration database backup instead."
        )

    products = sa.Table("products", sa.MetaData(), autoload_with=bind)
    rows = bind.execute(sa.select(products)).mappings().all()
    for table_name in CHILD_TABLES:
        _rebuild_child_without_fk(table_name, "product_key", drop_product_key=True)

    op.create_table(
        "products_legacy",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_products_legacy"),
    )
    for row in rows:
        bind.execute(sa.text(
            "INSERT INTO products_legacy (id, name, currency, unit_price, stock, tenant_id) "
            "VALUES (:id, :name, :currency, :unit_price, :stock, :tenant_id)"
        ), {"id": row["sku"], "name": row["name"], "currency": row["currency"],
            "unit_price": str(row["unit_price"]), "stock": row["stock"], "tenant_id": row["tenant_id"]})
    op.drop_table("products")
    op.rename_table("products_legacy", "products")
    for table_name in CHILD_TABLES:
        with op.batch_alter_table(table_name, naming_convention=FK_NAMING) as batch:
            batch.create_foreign_key(
                f"fk_{table_name}_product_id_products", "products", ["product_id"], ["id"],
            )
