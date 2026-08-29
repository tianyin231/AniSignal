"""store AI content review results

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    op.create_table(
        "content_reviews",
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("relevant", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("promotional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quality_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("category_hint", sa.String(length=32), nullable=True),
        sa.Column("duplicate_of_raw_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["raw_content_id"], ["raw_contents.id"],
            name="fk_content_reviews_raw_content_id_raw_contents", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_raw_id"], ["raw_contents.id"],
            name="fk_content_reviews_duplicate_of_raw_id_raw_contents", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("raw_content_id", name="pk_content_reviews"),
        **MYSQL_KW,
    )
    op.create_index(
        "ix_content_reviews_duplicate_of_raw_id",
        "content_reviews",
        ["duplicate_of_raw_id"],
    )
    op.create_index(
        "ix_content_reviews_eligible_score",
        "content_reviews",
        ["relevant", "promotional", "quality_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_reviews_eligible_score", table_name="content_reviews")
    op.drop_index("ix_content_reviews_duplicate_of_raw_id", table_name="content_reviews")
    op.drop_table("content_reviews")
