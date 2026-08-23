"""product stage evidence

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("stage_hint_reason", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("stage_hint_evidence_ids", sa.JSON(), nullable=True))
    op.add_column("products", sa.Column("stage_hint_updated_at", sa.DateTime(), nullable=True))
    # 旧的无证据人工阶段不再可信，统一恢复为自动计算。
    op.execute("UPDATE products SET stage_hint = -1 WHERE stage_hint >= 0")


def downgrade() -> None:
    op.drop_column("products", "stage_hint_updated_at")
    op.drop_column("products", "stage_hint_evidence_ids")
    op.drop_column("products", "stage_hint_reason")
