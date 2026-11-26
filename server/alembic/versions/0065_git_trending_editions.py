"""Add git_trending_editions table for GitHub Trending daily/weekly/monthly archive.

Revision ID: 0065
Revises: 0064
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "git_trending_editions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("community", sa.String(16), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("period_key", sa.String(32), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("community", "period", "period_key", "language", name="uq_git_trending_edition"),
    )
    op.create_index(
        "ix_git_trending_community_period",
        "git_trending_editions",
        ["community", "period", "period_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_git_trending_community_period", table_name="git_trending_editions")
    op.drop_table("git_trending_editions")
