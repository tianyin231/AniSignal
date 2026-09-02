"""multi-community partitions (鸣潮 / 碧蓝航线)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 判定为碧蓝航线分区的关键词（与 2026-09 服务器上已运行的采集目标一致）
_BLHX_KEYWORDS = ["碧蓝航线", "碧蓝航线官方账号动态"]


def _dialect() -> str:
    return op.get_bind().dialect.name


def _backfill_blhx_contents() -> None:
    """存量内容按命中关键词划入 blhx 分区，其余维持 wuva。

    MySQL 用 JSON_CONTAINS，SQLite 用 json_each（本地测试库）。
    """
    bind = op.get_bind()
    if _dialect() == "mysql":
        for keyword in _BLHX_KEYWORDS:
            bind.execute(
                sa.text(
                    "UPDATE raw_contents SET community='blhx' "
                    "WHERE community='wuwa' AND JSON_CONTAINS(matched_keywords, :kw)"
                ),
                {"kw": json.dumps(keyword, ensure_ascii=False)},
            )
    else:
        for keyword in _BLHX_KEYWORDS:
            bind.execute(
                sa.text(
                    "UPDATE raw_contents SET community='blhx' "
                    "WHERE community='wuwa' AND EXISTS ("
                    "SELECT 1 FROM json_each(raw_contents.matched_keywords) "
                    "WHERE json_each.value = :kw)"
                ),
                {"kw": keyword},
            )


def upgrade() -> None:
    # 存量全部回填 wuwa（默认分区），碧蓝航线关键词内容随后单独划出
    op.add_column(
        "collect_targets",
        sa.Column("community", sa.String(length=16), nullable=False, server_default="wuwa"),
    )
    op.add_column(
        "raw_contents",
        sa.Column("community", sa.String(length=16), nullable=False, server_default="wuwa"),
    )
    op.create_index("ix_raw_contents_community", "raw_contents", ["community"])
    op.add_column(
        "events",
        sa.Column("community", sa.String(length=16), nullable=False, server_default="wuwa"),
    )
    op.create_index("ix_events_community_active", "events", ["community", "is_active"])
    op.add_column(
        "products",
        sa.Column("community", sa.String(length=16), nullable=False, server_default="wuwa"),
    )
    # 分类词典：NULL = 全分区通用（履约词典等），存量规则全部置空保持跨分区可用
    op.add_column(
        "classification_rules",
        sa.Column("community", sa.String(length=16), nullable=True),
    )

    _backfill_blhx_contents()

    # 碧蓝航线官方账号动态目标（UID 已通过 bili CLI 核验：认证"碧蓝航线官方账号"）；
    # 已存在同键目标（如服务器手工建过）时跳过。INSERT..SELECT..WHERE NOT EXISTS 兼容 MySQL/SQLite。
    op.execute(
        "INSERT INTO collect_targets "
        "(platform, target_type, target_value, display_name, interval_minutes, enabled, "
        " co_terms, min_views, tier, community, created_at, updated_at) "
        "SELECT 'bilibili', 'account_dynamics', '233114659', '碧蓝航线官方账号动态', 30, 1, "
        " '[]', 0, 'core', 'blhx', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM collect_targets WHERE platform='bilibili' "
        "AND target_type='account_dynamics' AND target_value='233114659')"
    )


def downgrade() -> None:
    op.drop_column("classification_rules", "community")
    op.drop_column("products", "community")
    op.drop_index("ix_events_community_active", table_name="events")
    op.drop_column("events", "community")
    op.drop_index("ix_raw_contents_community", table_name="raw_contents")
    op.drop_column("raw_contents", "community")
    op.drop_column("collect_targets", "community")
