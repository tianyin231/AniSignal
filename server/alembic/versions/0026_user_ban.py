"""user ban audit fields (封禁原因与时间，status 列已存在于 0003)

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("banned_reason", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("banned_at", sa.DateTime(), nullable=True))
    # 万级用户的按状态筛选（管理端"只看封禁"）；status 基数低，普通索引足够
    op.create_index("ix_users_status", "users", ["status"])


def downgrade() -> None:
    op.drop_index("ix_users_status", table_name="users")
    op.drop_column("users", "banned_at")
    op.drop_column("users", "banned_reason")
