"""Add MySQL 8.4 ngram FULLTEXT indexes for Chinese/cross-language full-text search.

Revision ID: 0060
Revises: 0059
"""

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_FULLTEXT_INDEXES = (
    ("content_versions", "ft_content_versions_title_text", "title, text"),
    ("content_translations", "ft_content_translations_text", "text"),
    ("events", "ft_events_title_summary", "title, summary"),
)


def _inspector(bind: sa.Connection) -> sa.Inspector | None:
    if op.get_context().as_sql:
        return None
    return sa.inspect(bind)


def _has_index(inspector: sa.Inspector | None, table: str, name: str, *, default: bool = False) -> bool:
    if inspector is None:
        return default
    return any(idx["name"] == name for idx in inspector.get_indexes(table))


def _has_ngram_parser(bind: sa.Connection) -> bool:
    """检查当前数据库是否激活了 MySQL ngram 全文分词插件（MariaDB 或精简版 MySQL 无此插件）。"""
    if op.get_context().as_sql:
        return True
    try:
        active = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.plugins "
                "WHERE LOWER(plugin_name) = 'ngram' AND UPPER(plugin_status) = 'ACTIVE' "
                "LIMIT 1"
            )
        ).scalar()
        return bool(active)
    except Exception:  # noqa: BLE001 - 权限受限或非标准衍生库时安全跳过
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in ("mysql", "mariadb"):
        # SQLite（本地单测/免容器模式）走应用层 LIKE 回退，不创建 MySQL 专属 FULLTEXT 索引。
        return
    if not _has_ngram_parser(bind):
        logger.warning(
            "跳过 0060 ngram FULLTEXT 索引创建：当前数据库未激活 ngram 分词插件"
            "（如 MariaDB 或精简版 MySQL），站内搜索将在运行时自动回退安全 LIKE。"
        )
        return
    inspector = _inspector(bind)
    for table, index_name, columns in _FULLTEXT_INDEXES:
        if not _has_index(inspector, table, index_name):
            op.execute(sa.text(
                f"ALTER TABLE {table} ADD FULLTEXT INDEX {index_name} ({columns}) WITH PARSER ngram"
            ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name not in ("mysql", "mariadb"):
        return
    inspector = _inspector(bind)
    for table, index_name, _columns in reversed(_FULLTEXT_INDEXES):
        if _has_index(inspector, table, index_name, default=True):
            op.execute(sa.text(f"ALTER TABLE {table} DROP INDEX {index_name}"))
