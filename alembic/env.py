from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from selara.infrastructure.db.base import Base
from selara.infrastructure.db import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _read_database_url_from_dotenv() -> str | None:
    config_path = Path(config.config_file_name).resolve() if config.config_file_name else Path.cwd() / "alembic.ini"
    env_path = config_path.parent / ".env"

    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() != "DATABASE_URL":
            continue

        return value.strip().strip("\"'")

    return None


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _to_psycopg_url(url: str) -> str:
    sync_url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    parts = urlsplit(sync_url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))

    ssl_value = query_params.pop("ssl", None)
    if ssl_value is not None:
        mapping = {
            "disable": "disable",
            "allow": "allow",
            "prefer": "prefer",
            "require": "require",
            "verify-ca": "verify-ca",
            "verify-full": "verify-full",
            "true": "require",
            "false": "disable",
        }
        query_params["sslmode"] = mapping.get(ssl_value.lower(), ssl_value)

    new_query = urlencode(query_params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def get_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return _normalize_database_url(database_url)

    database_url = _read_database_url_from_dotenv()
    if database_url:
        return _normalize_database_url(database_url)

    return _normalize_database_url(config.get_main_option("sqlalchemy.url"))


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    url = configuration["sqlalchemy.url"]
    force_sync = os.getenv("ALEMBIC_USE_SYNC_DRIVER", "").lower() in {"1", "true", "yes"}

    if url.startswith("postgresql+asyncpg://") and not force_sync:
        connectable = async_engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        try:
            async with connectable.connect() as connection:
                await connection.run_sync(do_run_migrations)
            return
        except Exception:
            # asyncpg can fail on some Windows setups; fallback below.
            pass
        finally:
            await connectable.dispose()

    sync_configuration = dict(configuration)
    sync_configuration["sqlalchemy.url"] = _to_psycopg_url(sync_configuration["sqlalchemy.url"])
    connectable = engine_from_config(
        sync_configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_migrations_online())
