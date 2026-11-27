"""Add periodic_reports and report_subscriptions tables for general scheduled reports.

Revision ID: 0067
Revises: 0066
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "periodic_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("period_key", sa.String(32), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("overview", sa.Text(), nullable=False),
        sa.Column("sections", sa.JSON(), nullable=False),
        sa.Column("markdown", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("ai_generated", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_periodic_reports_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_key", "period", "period_key", name="uq_periodic_report"),
    )
    op.create_index("ix_periodic_reports_scope_created", "periodic_reports", ["scope_type", "scope_key", "created_at"])
    op.create_index("ix_periodic_reports_owner", "periodic_reports", ["owner_user_id", "created_at"])

    op.create_table(
        "report_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("webhook_id", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_period_key", sa.String(32), nullable=True),
        sa.Column("last_report_id", sa.Integer(), nullable=True),
        sa.Column("last_status", sa.String(16), nullable=True),
        sa.Column("last_error", sa.String(255), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_report_subscriptions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["user_webhooks.id"],
            name="fk_report_subscriptions_webhook_id_user_webhooks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_report_id"],
            ["periodic_reports.id"],
            name="fk_report_subscriptions_last_report_id_periodic_reports",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_subscriptions_user_id", "report_subscriptions", ["user_id"])
    op.create_index("ix_report_subscriptions_user_enabled", "report_subscriptions", ["user_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_report_subscriptions_user_enabled", table_name="report_subscriptions")
    op.drop_index("ix_report_subscriptions_user_id", table_name="report_subscriptions")
    op.drop_table("report_subscriptions")
    op.drop_index("ix_periodic_reports_owner", table_name="periodic_reports")
    op.drop_index("ix_periodic_reports_scope_created", table_name="periodic_reports")
    op.drop_table("periodic_reports")
