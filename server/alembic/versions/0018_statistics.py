"""add pre-aggregated public statistics

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    for name in (
        "items_fetched",
        "items_filtered",
        "items_updated",
        "parse_failed",
        "official_items",
        "community_items",
        "duration_ms",
    ):
        op.add_column("collect_runs", sa.Column(name, sa.Integer(), nullable=True))
    op.create_index(
        "ix_collect_runs_started_status_target",
        "collect_runs",
        ["started_at", "status", "target_id"],
    )

    op.create_table(
        "content_daily_stats",
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("identity", sa.String(length=16), nullable=False),
        sa.Column("keyword", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("contents_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint(
            "stat_date", "platform", "identity", "keyword",
            name="pk_content_daily_stats",
        ),
        **MYSQL_KW,
    )
    op.create_index(
        "ix_content_daily_stats_keyword_date",
        "content_daily_stats",
        ["keyword", "stat_date"],
    )
    op.create_index(
        "ix_content_daily_stats_date_keyword",
        "content_daily_stats",
        ["stat_date", "keyword"],
    )

    op.create_table(
        "collection_daily_stats",
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detailed_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_filtered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parse_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("official_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("community_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_id"], ["collect_targets.id"],
            name="fk_collection_daily_stats_target_id_collect_targets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "stat_date", "target_id", "status",
            name="pk_collection_daily_stats",
        ),
        **MYSQL_KW,
    )
    op.create_index(
        "ix_collection_daily_stats_date_status",
        "collection_daily_stats",
        ["stat_date", "status"],
    )

    # 线上存量很小，使用集合式 SQL 一次回填；运行期接口不再扫描原始表。
    if op.get_bind().dialect.name == "mysql":
        op.execute(
            """
            INSERT INTO content_daily_stats
                (stat_date, platform, identity, keyword, contents_new, updated_at)
            SELECT DATE(DATE_ADD(r.collected_at, INTERVAL 8 HOUR)), r.platform,
                   COALESCE(s.identity, 'unknown'), '', COUNT(*), UTC_TIMESTAMP()
            FROM raw_contents r
            LEFT JOIN sources s ON s.id = r.source_id
            GROUP BY DATE(DATE_ADD(r.collected_at, INTERVAL 8 HOUR)),
                     r.platform, COALESCE(s.identity, 'unknown')
            """
        )
        op.execute(
            """
            INSERT INTO content_daily_stats
                (stat_date, platform, identity, keyword, contents_new, updated_at)
            SELECT DATE(DATE_ADD(r.collected_at, INTERVAL 8 HOUR)), r.platform,
                   COALESCE(s.identity, 'unknown'), LEFT(j.keyword_value, 255),
                   COUNT(*), UTC_TIMESTAMP()
            FROM raw_contents r
            LEFT JOIN sources s ON s.id = r.source_id
            JOIN JSON_TABLE(
                COALESCE(r.matched_keywords, JSON_ARRAY()),
                '$[*]' COLUMNS(keyword_value VARCHAR(255) PATH '$')
            ) AS j
            WHERE j.keyword_value <> ''
            GROUP BY DATE(DATE_ADD(r.collected_at, INTERVAL 8 HOUR)),
                     r.platform, COALESCE(s.identity, 'unknown'), LEFT(j.keyword_value, 255)
            """
        )
        op.execute(
            """
            INSERT INTO collection_daily_stats
                (stat_date, target_id, status, runs, detailed_runs,
                 items_fetched, items_filtered, items_found, items_new,
                 items_updated, parse_failed, official_items, community_items,
                 duration_ms, updated_at)
            SELECT DATE(DATE_ADD(COALESCE(finished_at, started_at), INTERVAL 8 HOUR)),
                   target_id, status, COUNT(*), 0, 0, 0,
                   SUM(items_found), SUM(items_new), 0, 0, 0, 0, 0, UTC_TIMESTAMP()
            FROM collect_runs
            WHERE status <> 'running'
            GROUP BY DATE(DATE_ADD(COALESCE(finished_at, started_at), INTERVAL 8 HOUR)),
                     target_id, status
            """
        )


def downgrade() -> None:
    op.drop_index("ix_collection_daily_stats_date_status", table_name="collection_daily_stats")
    op.drop_table("collection_daily_stats")
    op.drop_index("ix_content_daily_stats_date_keyword", table_name="content_daily_stats")
    op.drop_index("ix_content_daily_stats_keyword_date", table_name="content_daily_stats")
    op.drop_table("content_daily_stats")
    op.drop_index("ix_collect_runs_started_status_target", table_name="collect_runs")
    for name in reversed((
        "items_fetched",
        "items_filtered",
        "items_updated",
        "parse_failed",
        "official_items",
        "community_items",
        "duration_ms",
    )):
        op.drop_column("collect_runs", name)
