"""Alembic environment. The database URL comes from app settings (and the
postgres_password secret), never from alembic.ini."""

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings

target_metadata = None


def run_migrations_offline() -> None:
    url = get_settings().database_url.render_as_string(hide_password=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
