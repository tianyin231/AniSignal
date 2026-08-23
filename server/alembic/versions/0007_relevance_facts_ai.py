"""relevance filters, event facts, content translations, ai settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("collect_targets", sa.Column("co_terms", sa.JSON(), nullable=True))
    op.add_column("collect_targets", sa.Column("min_views", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("events", sa.Column("facts", sa.JSON(), nullable=True))
    op.create_table(
        "content_translations",
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False, server_default="zh-CN"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["raw_content_id"],
            ["raw_contents.id"],
            name="fk_content_translations_raw_content_id_raw_contents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("raw_content_id", name="pk_content_translations"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade() -> None:
    op.drop_table("content_translations")
    op.drop_column("events", "facts")
    op.drop_column("collect_targets", "min_views")
    op.drop_column("collect_targets", "co_terms")
