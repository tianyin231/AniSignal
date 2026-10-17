"""规则引擎、声量基线与信源分级：规则表、命中台账、滑动窗口基线、内容规则标签与来源档位。

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("keyword_expr", sa.Text(), nullable=True),
        sa.Column("platforms", sa.JSON(), nullable=True),
        sa.Column("communities", sa.JSON(), nullable=True),
        sa.Column("intent_tiers", sa.JSON(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_rules_enabled_priority", "rules", ["enabled", "priority"])

    op.create_table(
        "rule_hits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("rules.id", name="fk_rule_hits_rule_id_rules", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_id", sa.Integer(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("rule_id", "content_id", name="uq_rule_hit"),
    )
    op.create_index("ix_rule_hits_rule_id", "rule_hits", ["rule_id"])
    op.create_index("ix_rule_hits_content_id", "rule_hits", ["content_id"])
    op.create_index("ix_rule_hits_created", "rule_hits", ["created_at"])

    op.create_table(
        "volume_baselines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_key", sa.String(length=190), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("keyword", sa.String(length=255), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mean", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stddev", sa.Float(), nullable=False, server_default="0"),
        sa.Column("samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("multiplier", sa.Float(), nullable=False, server_default="3"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("group_key", "window_started_at", name="uq_volume_window"),
    )
    op.create_index("ix_volume_baselines_status", "volume_baselines", ["status"])
    op.create_index(
        "ix_volume_baselines_group", "volume_baselines", ["group_key", "window_started_at"]
    )

    op.add_column("raw_contents", sa.Column("rule_tags", sa.JSON(), nullable=True))
    op.add_column(
        "raw_contents",
        sa.Column("rule_acked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("raw_contents", sa.Column("rule_acked_at", sa.DateTime(), nullable=True))
    op.create_index("ix_raw_contents_rule_acked", "raw_contents", ["rule_acked"])

    op.add_column(
        "sources",
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="unknown"),
    )
    op.create_index("ix_sources_tier", "sources", ["tier"])


def downgrade() -> None:
    op.drop_index("ix_sources_tier", table_name="sources")
    op.drop_column("sources", "tier")
    op.drop_index("ix_raw_contents_rule_acked", table_name="raw_contents")
    op.drop_column("raw_contents", "rule_acked_at")
    op.drop_column("raw_contents", "rule_acked")
    op.drop_column("raw_contents", "rule_tags")
    op.drop_index("ix_volume_baselines_group", table_name="volume_baselines")
    op.drop_index("ix_volume_baselines_status", table_name="volume_baselines")
    op.drop_table("volume_baselines")
    op.drop_index("ix_rule_hits_created", table_name="rule_hits")
    op.drop_index("ix_rule_hits_content_id", table_name="rule_hits")
    op.drop_index("ix_rule_hits_rule_id", table_name="rule_hits")
    op.drop_table("rule_hits")
    op.drop_index("ix_rules_enabled_priority", table_name="rules")
    op.drop_table("rules")
