"""content moderation: hidden flag and violation review verdict

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 存量行回填默认值：hidden=false（全部保持可见），violation=false
    op.add_column(
        "raw_contents",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_raw_contents_hidden", "raw_contents", ["hidden"])
    op.add_column(
        "content_reviews",
        sa.Column("violation", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("content_reviews", "violation")
    op.drop_index("ix_raw_contents_hidden", table_name="raw_contents")
    op.drop_column("raw_contents", "hidden")
