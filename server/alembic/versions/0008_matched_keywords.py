"""matched_keywords + co_terms backfill

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("raw_contents", sa.Column("matched_keywords", sa.JSON(), nullable=True))
    # 0007 加列时未回填：存量行 co_terms 为 NULL，序列化层期望列表（曾致 /api/admin/overview 500）
    op.execute("UPDATE collect_targets SET co_terms = '[]' WHERE co_terms IS NULL")
    op.execute("UPDATE collect_targets SET min_views = 0 WHERE min_views IS NULL")


def downgrade() -> None:
    op.drop_column("raw_contents", "matched_keywords")
