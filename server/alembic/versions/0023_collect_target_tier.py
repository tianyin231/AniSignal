"""collect target admission tier

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 存量目标回填 general（维持现状：泛关键词走热度门槛）
    op.add_column(
        "collect_targets",
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="general"),
    )


def downgrade() -> None:
    op.drop_column("collect_targets", "tier")
