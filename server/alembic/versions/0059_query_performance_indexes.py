"""Add raw_contents.latest_version_id, metric_snapshots.view_count, and hot query composite indexes.

Revision ID: 0059
Revises: 0058
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BACKFILL_BATCH = 20_000


def _inspector(bind: sa.Connection) -> sa.Inspector | None:
    if op.get_context().as_sql:
        return None
    return sa.inspect(bind)


def _has_column(inspector: sa.Inspector | None, table: str, column: str, *, default: bool = False) -> bool:
    if inspector is None:
        return default
    return any(col["name"] == column for col in inspector.get_columns(table))


def _has_index(inspector: sa.Inspector | None, table: str, name: str, *, default: bool = False) -> bool:
    if inspector is None:
        return default
    return any(idx["name"] == name for idx in inspector.get_indexes(table))


def _backfill_latest_version_id(bind: sa.Connection) -> None:
    if bind.dialect.name == "mysql":
        op.execute(sa.text("""
            UPDATE raw_contents rc
            JOIN (
                SELECT cv.raw_content_id, MAX(cv.id) AS id
                FROM content_versions cv
                JOIN (
                    SELECT raw_content_id, MAX(version) AS max_v
                    FROM content_versions
                    GROUP BY raw_content_id
                ) mx ON mx.raw_content_id = cv.raw_content_id AND mx.max_v = cv.version
                GROUP BY cv.raw_content_id
            ) latest ON latest.raw_content_id = rc.id
            SET rc.latest_version_id = latest.id
            WHERE rc.latest_version_id IS NULL
        """))
    else:
        op.execute(sa.text("""
            UPDATE raw_contents
            SET latest_version_id = (
                SELECT cv.id FROM content_versions cv
                WHERE cv.raw_content_id = raw_contents.id
                ORDER BY cv.version DESC, cv.id DESC
                LIMIT 1
            )
            WHERE latest_version_id IS NULL
        """))


def _backfill_metric_view_count(bind: sa.Connection) -> None:
    mysql_expr = """
        CASE
            WHEN JSON_TYPE(JSON_EXTRACT(metrics, '$.view')) IN ('INTEGER', 'DOUBLE', 'DECIMAL')
                THEN CAST(JSON_EXTRACT(metrics, '$.view') AS SIGNED)
            WHEN JSON_TYPE(JSON_EXTRACT(metrics, '$.view')) = 'STRING'
                 AND JSON_UNQUOTE(JSON_EXTRACT(metrics, '$.view')) REGEXP '^-?[0-9]+$'
                THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(metrics, '$.view')) AS SIGNED)
            ELSE NULL
        END
    """
    if op.get_context().as_sql:
        if bind.dialect.name == "mysql":
            op.execute(sa.text(f"UPDATE metric_snapshots SET view_count = {mysql_expr} WHERE view_count IS NULL"))
        else:
            op.execute(sa.text("""
                UPDATE metric_snapshots
                SET view_count = CAST(json_extract(metrics, '$.view') AS INTEGER)
                WHERE view_count IS NULL AND json_extract(metrics, '$.view') IS NOT NULL
            """))
        return

    max_id = bind.execute(sa.text("SELECT COALESCE(MAX(id), 0) FROM metric_snapshots")).scalar() or 0
    if not max_id:
        return
    if bind.dialect.name == "mysql":
        # 按主键分批回填 50 万+ 存量快照，避免单次大事务持锁过久。
        cursor = 0
        while cursor < max_id:
            upper = cursor + _BACKFILL_BATCH
            bind.execute(
                sa.text(f"""
                    UPDATE metric_snapshots
                    SET view_count = {mysql_expr}
                    WHERE id > :lo AND id <= :hi AND view_count IS NULL
                """),
                {"lo": cursor, "hi": upper},
            )
            cursor = upper
    else:
        bind.execute(sa.text("""
            UPDATE metric_snapshots
            SET view_count = CAST(json_extract(metrics, '$.view') AS INTEGER)
            WHERE view_count IS NULL
              AND json_extract(metrics, '$.view') IS NOT NULL
        """))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    # 1. raw_contents.latest_version_id + 回填 + 高频分页复合索引
    if not _has_column(inspector, "raw_contents", "latest_version_id"):
        op.add_column("raw_contents", sa.Column("latest_version_id", sa.Integer(), nullable=True))
    _backfill_latest_version_id(bind)
    inspector = _inspector(bind)
    if not _has_index(inspector, "raw_contents", "ix_raw_contents_latest_version_id"):
        op.create_index("ix_raw_contents_latest_version_id", "raw_contents", ["latest_version_id"], unique=False)
    if not _has_index(inspector, "raw_contents", "ix_raw_contents_comm_hidden_pub"):
        op.create_index(
            "ix_raw_contents_comm_hidden_pub",
            "raw_contents",
            ["community", "hidden", "published_at", "id"],
            unique=False,
        )
    if not _has_index(inspector, "raw_contents", "ix_raw_contents_hidden_pub"):
        op.create_index(
            "ix_raw_contents_hidden_pub",
            "raw_contents",
            ["hidden", "published_at", "id"],
            unique=False,
        )

    # 2. metric_snapshots.view_count + 分批回填 + 覆盖索引
    if not _has_column(inspector, "metric_snapshots", "view_count"):
        op.add_column("metric_snapshots", sa.Column("view_count", sa.BigInteger(), nullable=True))
    _backfill_metric_view_count(bind)
    inspector = _inspector(bind)
    if not _has_index(inspector, "metric_snapshots", "ix_metric_snapshots_content_id_view"):
        op.create_index(
            "ix_metric_snapshots_content_id_view",
            "metric_snapshots",
            ["raw_content_id", "id", "view_count"],
            unique=False,
        )

    # 3. content_matches 复合索引（消除聚合全表扫描）
    if not _has_index(inspector, "content_matches", "ix_content_matches_same_conf_pair"):
        op.create_index(
            "ix_content_matches_same_conf_pair",
            "content_matches",
            ["is_same_event", "confidence", "left_raw_id", "right_raw_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    if _has_index(inspector, "content_matches", "ix_content_matches_same_conf_pair", default=True):
        op.drop_index("ix_content_matches_same_conf_pair", table_name="content_matches")

    if _has_index(inspector, "metric_snapshots", "ix_metric_snapshots_content_id_view", default=True):
        op.drop_index("ix_metric_snapshots_content_id_view", table_name="metric_snapshots")
    if _has_column(inspector, "metric_snapshots", "view_count", default=True):
        op.drop_column("metric_snapshots", "view_count")

    for idx in (
        "ix_raw_contents_hidden_pub",
        "ix_raw_contents_comm_hidden_pub",
        "ix_raw_contents_latest_version_id",
    ):
        if _has_index(inspector, "raw_contents", idx, default=True):
            op.drop_index(idx, table_name="raw_contents")
    if _has_column(inspector, "raw_contents", "latest_version_id", default=True):
        op.drop_column("raw_contents", "latest_version_id")
