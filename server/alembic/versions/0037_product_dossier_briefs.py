"""商品档案与每日情报简报（批次 3b/3c）：档案头、价格热度曲线、质量反馈聚合、用户跟踪、到价提醒、日报表。

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 批次 3b：商品档案 ----
    op.create_table(
        "product_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", name="fk_product_profiles_product_id_products", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("official_url", sa.String(length=512), nullable=True),
        sa.Column("official_name", sa.String(length=255), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("release_at", sa.DateTime(), nullable=True),
        sa.Column("release_state", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("release_note", sa.String(length=255), nullable=True),
        sa.Column("price_current", sa.Float(), nullable=True),
        sa.Column("price_currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("price_source", sa.String(length=64), nullable=True),
        sa.Column("price_observed_at", sa.DateTime(), nullable=True),
        sa.Column("heat_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "product_price_points",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", name="fk_product_price_points_product_id_products", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("heat", sa.Float(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("note", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_product_price_points_product_id", "product_price_points", ["product_id"])
    op.create_index(
        "ix_product_price_points_product_time", "product_price_points", ["product_id", "captured_at"]
    )

    op.create_table(
        "product_feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", name="fk_product_feedbacks_product_id_products", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "content_id",
            sa.Integer(),
            sa.ForeignKey("raw_contents.id", name="fk_product_feedbacks_content_id_raw_contents", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sentiment", sa.String(length=16), nullable=False, server_default="negative"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("quote", sa.String(length=500), nullable=True),
        sa.Column("source_method", sa.String(length=16), nullable=False, server_default="ai"),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("product_id", "content_id", name="uq_product_feedback_content"),
    )
    op.create_index("ix_product_feedbacks_product_id", "product_feedbacks", ["product_id"])
    op.create_index(
        "ix_product_feedbacks_product_sentiment", "product_feedbacks", ["product_id", "sentiment"]
    )

    op.create_table(
        "product_track_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_product_track_states_user_id_users", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", name="fk_product_track_states_product_id_products", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="watching"),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("marked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "product_id", name="uq_product_track_state"),
    )
    op.create_index("ix_product_track_states_user_id", "product_track_states", ["user_id"])
    op.create_index("ix_product_track_states_product_id", "product_track_states", ["product_id"])
    op.create_index(
        "ix_product_track_states_product_state", "product_track_states", ["product_id", "state"]
    )

    op.create_table(
        "product_price_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_product_price_alerts_user_id_users", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", name="fk_product_price_alerts_product_id_products", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_price", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("triggered_at", sa.DateTime(), nullable=True),
        sa.Column("triggered_price", sa.Float(), nullable=True),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "product_id", name="uq_product_price_alert"),
    )
    op.create_index("ix_product_price_alerts_user_id", "product_price_alerts", ["user_id"])
    op.create_index("ix_product_price_alerts_product_id", "product_price_alerts", ["product_id"])
    op.create_index(
        "ix_product_price_alerts_product_enabled", "product_price_alerts", ["product_id", "enabled"]
    )

    # ---- 批次 3c：每日情报简报 ----
    op.create_table(
        "daily_briefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("sections", sa.JSON(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
        sa.Column("ai_status", sa.String(length=16), nullable=False, server_default="fallback"),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("delivered", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("brief_date", name="uq_daily_brief_date"),
    )
    op.create_index("ix_daily_briefs_created", "daily_briefs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_daily_briefs_created", table_name="daily_briefs")
    op.drop_table("daily_briefs")

    op.drop_index("ix_product_price_alerts_product_enabled", table_name="product_price_alerts")
    op.drop_index("ix_product_price_alerts_product_id", table_name="product_price_alerts")
    op.drop_index("ix_product_price_alerts_user_id", table_name="product_price_alerts")
    op.drop_table("product_price_alerts")

    op.drop_index("ix_product_track_states_product_state", table_name="product_track_states")
    op.drop_index("ix_product_track_states_product_id", table_name="product_track_states")
    op.drop_index("ix_product_track_states_user_id", table_name="product_track_states")
    op.drop_table("product_track_states")

    op.drop_index("ix_product_feedbacks_product_sentiment", table_name="product_feedbacks")
    op.drop_index("ix_product_feedbacks_product_id", table_name="product_feedbacks")
    op.drop_table("product_feedbacks")

    op.drop_index("ix_product_price_points_product_time", table_name="product_price_points")
    op.drop_index("ix_product_price_points_product_id", table_name="product_price_points")
    op.drop_table("product_price_points")

    op.drop_table("product_profiles")
