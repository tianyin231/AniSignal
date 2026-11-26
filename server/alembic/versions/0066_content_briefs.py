"""Add content_briefs table for AI structured Chinese briefs on feed & open-source items.

Revision ID: 0066
Revises: 0065
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "content_briefs",
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("headline", sa.String(120), nullable=False),
        sa.Column("tagline", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("highlights", sa.JSON(), nullable=False),
        sa.Column("use_cases", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_content_id"],
            ["raw_contents.id"],
            name="fk_content_briefs_raw_content_id_raw_contents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("raw_content_id"),
    )
    op.create_index("ix_content_briefs_updated_at", "content_briefs", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_content_briefs_updated_at", table_name="content_briefs")
    op.drop_table("content_briefs")
