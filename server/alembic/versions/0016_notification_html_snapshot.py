"""store rendered notification html

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_deliveries",
        sa.Column("html_body", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_deliveries", "html_body")
