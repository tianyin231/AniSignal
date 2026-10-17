"""RSS feed 只读令牌与通用 Webhook 通知渠道（含投递记录）。

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feed_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("kind", "ref_id", name="uq_feed_token_ref"),
        sa.UniqueConstraint("token", name="uq_feed_token_value"),
    )

    op.create_table(
        "webhook_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_webhook_configs_user_id_users", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False, server_default="Webhook"),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tier_filter", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_webhook_configs_user", "webhook_configs", ["user_id"])
    op.create_index("ix_webhook_configs_enabled", "webhook_configs", ["enabled"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "webhook_id",
            sa.Integer(),
            sa.ForeignKey(
                "webhook_configs.id",
                name="fk_webhook_deliveries_webhook_id_webhook_configs",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="hit"),
        sa.Column("content_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_webhook_delivery_dedupe"),
    )
    op.create_index(
        "ix_webhook_deliveries_hook_created", "webhook_deliveries", ["webhook_id", "created_at"]
    )
    op.create_index("ix_webhook_deliveries_user_id", "webhook_deliveries", ["user_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_user_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_hook_created", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_configs_enabled", table_name="webhook_configs")
    op.drop_index("ix_webhook_configs_user", table_name="webhook_configs")
    op.drop_table("webhook_configs")
    op.drop_table("feed_tokens")
