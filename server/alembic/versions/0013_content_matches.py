"""persist llm content match decisions

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("left_raw_id", sa.Integer(), nullable=False),
        sa.Column("right_raw_id", sa.Integer(), nullable=False),
        sa.Column("is_same_event", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["left_raw_id"], ["raw_contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["right_raw_id"], ["raw_contents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("left_raw_id", "right_raw_id", name="uq_content_match_pair"),
    )
    op.create_index("ix_content_matches_left_raw_id", "content_matches", ["left_raw_id"])
    op.create_index("ix_content_matches_right_raw_id", "content_matches", ["right_raw_id"])
    op.create_index(
        "ix_content_matches_same_confidence",
        "content_matches",
        ["is_same_event", "confidence"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_matches_same_confidence", table_name="content_matches")
    op.drop_index("ix_content_matches_right_raw_id", table_name="content_matches")
    op.drop_index("ix_content_matches_left_raw_id", table_name="content_matches")
    op.drop_table("content_matches")
