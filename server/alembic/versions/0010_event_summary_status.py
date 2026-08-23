"""event summary status

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("summary_status", sa.String(length=16), nullable=True))
    op.add_column("events", sa.Column("summary_updated_at", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE events SET summary_status = 'legacy', summary_updated_at = last_updated_at "
        "WHERE summary IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("events", "summary_updated_at")
    op.drop_column("events", "summary_status")
