"""Add MySQL 8.4 ngram FULLTEXT indexes for Chinese/cross-language full-text search.

Revision ID: 0060
Revises: 0059
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        # SQLite（本地单测）走应用层 LIKE 回退，不创建 MySQL 专属 FULLTEXT 索引。
        return
    inspector = _inspector(bind)
    for table, index_name, columns in _FULLTEXT_INDEXES:
        if not _has_index(inspector, table, index_name):
            op.execute(sa.text(
                f"ALTER TABLE {table} ADD FULLTEXT INDEX {index_name} ({columns}) WITH PARSER ngram"
            ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    inspector = _inspector(bind)
    for table, index_name, _columns in reversed(_FULLTEXT_INDEXES):
        if _has_index(inspector, table, index_name, default=True):
            op.execute(sa.text(f"ALTER TABLE {table} DROP INDEX {index_name}"))
