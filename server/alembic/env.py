"""Alembic 环境：同步引擎跑迁移（asyncmy URL 自动转 pymysql）。"""

import os

from alembic import context
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.models import Base

config = context.config

target_metadata = Base.metadata


def _sync_url() -> str:
    """ANISIGNAL_DATABASE_URL 是 async URL（mysql+asyncmy / sqlite+aiosqlite），迁移转同步驱动。"""
    if os.environ.get("ANISIGNAL_DATABASE_URL") or os.environ.get("ANISIGNAL_DATABASE_PASSWORD"):
        url = get_settings().resolved_database_url
    else:
        url = config.get_main_option("sqlalchemy.url")
    return url.replace("+asyncmy", "+pymysql").replace("+aiosqlite", "")


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
    is_mysql = engine.dialect.name == "mysql"
    lock_conn = None
    try:
        if is_mysql:
            # 独立 AUTOCOMMIT 连接持有会话级 GET_LOCK，避免触发迁移连接的 SQLAlchemy 2.0 autobegin 导致 alembic_version 回滚
            lock_conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
            got = lock_conn.scalar(text("SELECT GET_LOCK('anisignal_alembic', 30)"))
            if got != 1:
                raise RuntimeError("未能获取数据库迁移锁 anisignal_alembic，可能有其他实例正在迁移")
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            if connection.in_transaction():
                connection.commit()
    finally:
        if lock_conn is not None:
            try:
                lock_conn.execute(text("SELECT RELEASE_LOCK('anisignal_alembic')"))
            finally:
                lock_conn.close()
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
