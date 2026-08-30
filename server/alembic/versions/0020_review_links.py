"""review links extracted by AI

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 可空 JSON 列：NULL 表示尚未由 AI 提取，公开侧回退规则提取
    op.add_column("content_reviews", sa.Column("links", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("content_reviews", "links")
