"""Alembic 环境：同步引擎跑迁移（asyncmy URL 自动转 pymysql）。"""

import os

from alembic import context
from sqlalchemy import create_engine

from app.models import Base

config = context.config

target_metadata = Base.metadata


def _sync_url() -> str:
    """ANISIGNAL_DATABASE_URL 是 async URL（mysql+asyncmy），迁移用 pymysql。"""
    url = os.environ.get("ANISIGNAL_DATABASE_URL") or config.get_main_option(
        "sqlalchemy.url"
    )
    return url.replace("+asyncmy", "+pymysql")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
