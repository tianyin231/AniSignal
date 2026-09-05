"""custom feeds (用户定制监控：UID/关键词抓取源与条目)

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_feeds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_custom_feeds_user_id_users", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False, server_default="bilibili"),
        sa.Column("target_value", sa.String(length=128), nullable=False),
        sa.Column("min_views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_collected_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_custom_feeds_next_run", "custom_feeds", ["next_run_at"])
    op.create_index("ix_custom_feeds_user", "custom_feeds", ["user_id"])

    op.create_table(
        "custom_feed_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "feed_id",
            sa.Integer(),
            sa.ForeignKey(
                "custom_feeds.id", name="fk_custom_feed_items_feed_id_custom_feeds", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=16), nullable=False, server_default="video"),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("cover", sa.String(length=512), nullable=True),
        sa.Column("author_name", sa.String(length=64), nullable=True),
        sa.Column("view", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("collected_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("feed_id", "external_id", name="uq_custom_feed_item"),
    )
    op.create_index("ix_custom_feed_items_feed_time", "custom_feed_items", ["feed_id", "published_at"])


def downgrade() -> None:
    op.drop_index("ix_custom_feed_items_feed_time", table_name="custom_feed_items")
    op.drop_table("custom_feed_items")
    op.drop_index("ix_custom_feeds_user", table_name="custom_feeds")
    op.drop_index("ix_custom_feeds_next_run", table_name="custom_feeds")
    op.drop_table("custom_feeds")
