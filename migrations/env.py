"""Alembic environment for PostgreSQL; never embed credentials in migrations."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def db_url():
    return URL.create(
        "postgresql+psycopg2",
        username=os.getenv("POSTGRES_USER", "tramitia"),
        password=os.getenv("POSTGRES_PASSWORD", "tramitia"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "tramitia"),
    )


if context.is_offline_mode():
    context.configure(
        url=db_url().render_as_string(hide_password=False),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(db_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
