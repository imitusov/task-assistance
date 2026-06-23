from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import config

config_ = context.config

if config_.config_file_name is not None:
    fileConfig(config_.config_file_name)

target_metadata = None


def _psycopg2_url() -> str:
    return config.DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg2://", 1
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_psycopg2_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config_.get_section(config_.config_ini_section, {})
    configuration["sqlalchemy.url"] = _psycopg2_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
