from logging.config import fileConfig
import os
from alembic import context
from sqlalchemy import engine_from_config, inspect, pool
from app.db.database import Base
from app.db import models  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url = os.getenv("DEALGUARD_MIGRATION_URL")
if url:
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            tables = inspect(connection).get_table_names()
            if "alembic_version" in tables:
                version_column = next(column for column in inspect(connection).get_columns("alembic_version") if column["name"] == "version_num")
                if getattr(version_column["type"], "length", 0) < 64:
                    connection.exec_driver_sql("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
