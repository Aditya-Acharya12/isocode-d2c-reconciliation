from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Any fixed number shared by every process that migrates this database.
MIGRATION_LOCK_KEY = 738201946
# Long enough for a real migration, while a stuck deploy still fails visibly.
LOCK_TIMEOUT = "120s"


def run_migrations_online() -> None:
    """Runs migrations while holding a Postgres advisory lock.

    Migrations run in the start command, and during a deploy an old and a new
    instance can start together. The lock makes one wait for the other, and the
    waiter then finds nothing left to do. Postgres releases the lock if the holder's
    connection dies, so a crash can't leave the database locked.

    Session-level locks don't work through Neon's transaction-mode pooler, so this
    must use the direct URL. The lock is held on its own autocommit connection,
    which lets the migration commit without affecting the lock.
    """
    url = config.attributes.get("database_url") or get_settings().sqlalchemy_direct_url
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 15})

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_connection:
        lock_connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
        lock_connection.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        try:
            with engine.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    compare_type=True,
                )
                with context.begin_transaction():
                    context.run_migrations()
                connection.commit()
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )


if context.is_offline_mode():
    raise RuntimeError("Offline migrations aren't supported: the advisory lock needs a database.")

run_migrations_online()
