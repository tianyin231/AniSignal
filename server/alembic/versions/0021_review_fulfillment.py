"""AI fulfillment pre-judgment on content reviews

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 可空 JSON 列：NULL 表示该内容不涉及商品履约或尚未预判
    op.add_column("content_reviews", sa.Column("fulfillment", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("content_reviews", "fulfillment")
