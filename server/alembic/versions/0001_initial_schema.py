"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-22

首版六表：collect_targets / sources / raw_contents / content_versions /
metric_snapshots / collect_runs（见 docs 设计文档 §7 与项目 skill）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_KW = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "collect_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_value", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_collect_targets"),
        sa.UniqueConstraint("platform", "target_type", "target_value", name="uq_target"),
        **MYSQL_KW,
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("official_title", sa.String(length=255), nullable=True),
        sa.Column("follower_count", sa.BigInteger(), nullable=True),
        sa.Column("identity", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.UniqueConstraint("platform", "external_id", name="uq_source"),
        **MYSQL_KW,
    )

    op.create_table(
        "raw_contents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("content_type", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("collected_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_raw_contents_source_id_sources"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_raw_contents"),
        sa.UniqueConstraint("platform", "external_id", name="uq_raw_content"),
        **MYSQL_KW,
    )
    op.create_index(
        "ix_raw_contents_collected_at", "raw_contents", ["collected_at"], unique=False
    )

    op.create_table(
        "content_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("normalized_json", sa.JSON(), nullable=True),
        sa.Column("parser_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["raw_content_id"],
            ["raw_contents.id"],
            name="fk_content_versions_raw_content_id_raw_contents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_content_versions"),
        sa.UniqueConstraint("raw_content_id", "version", name="uq_content_version"),
        **MYSQL_KW,
    )
    op.create_index(
        "ix_content_versions_raw_content_id",
        "content_versions",
        ["raw_content_id"],
        unique=False,
    )

    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["raw_content_id"],
            ["raw_contents.id"],
            name="fk_metric_snapshots_raw_content_id_raw_contents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metric_snapshots"),
        **MYSQL_KW,
    )
    op.create_index(
        "ix_metric_snapshots_content_time",
        "metric_snapshots",
        ["raw_content_id", "captured_at"],
        unique=False,
    )

    op.create_table(
        "collect_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("items_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["collect_targets.id"],
            name="fk_collect_runs_target_id_collect_targets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collect_runs"),
        **MYSQL_KW,
    )
    op.create_index("ix_collect_runs_target_id", "collect_runs", ["target_id"], unique=False)


def downgrade() -> None:
    op.drop_table("collect_runs")
    op.drop_table("metric_snapshots")
    op.drop_table("content_versions")
    op.drop_table("raw_contents")
    op.drop_table("sources")
    op.drop_table("collect_targets")
