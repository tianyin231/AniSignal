"""Add steam_app_snapshots table for Steam CCU and review pulse telemetry.

Revision ID: 0058
Revises: 0057
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "steam_app_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("community", sa.String(length=16), nullable=False),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("player_count", sa.Integer(), nullable=True),
        sa.Column("global_positive", sa.Integer(), nullable=True),
        sa.Column("global_negative", sa.Integer(), nullable=True),
        sa.Column("global_score_desc", sa.String(length=48), nullable=True),
        sa.Column("cn_positive", sa.Integer(), nullable=True),
        sa.Column("cn_negative", sa.Integer(), nullable=True),
        sa.Column("price_formatted", sa.String(length=64), nullable=True),
        sa.Column("discount_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_steam_app_snapshots")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_steam_snapshots_captured_at", "steam_app_snapshots", ["captured_at"], unique=False)
    op.create_index(
        "ix_steam_snapshots_comm_app_time",
        "steam_app_snapshots",
        ["community", "app_id", "captured_at"],
        unique=False,
    )
    op.create_index(op.f("ix_steam_app_snapshots_app_id"), "steam_app_snapshots", ["app_id"], unique=False)
    op.create_index(op.f("ix_steam_app_snapshots_community"), "steam_app_snapshots", ["community"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_steam_app_snapshots_community"), table_name="steam_app_snapshots")
    op.drop_index(op.f("ix_steam_app_snapshots_app_id"), table_name="steam_app_snapshots")
    op.drop_index("ix_steam_snapshots_comm_app_time", table_name="steam_app_snapshots")
    op.drop_index("ix_steam_snapshots_captured_at", table_name="steam_app_snapshots")
    op.drop_table("steam_app_snapshots")
