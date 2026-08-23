"""users: display_name + avatar

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("avatar", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar")
    op.drop_column("users", "display_name")
