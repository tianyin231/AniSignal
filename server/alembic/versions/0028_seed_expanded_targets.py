"""seed expanded targets (碧蓝航线补齐 + 崩坏星穹铁道分区监视源)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-05

官方身份核验记录（bili CLI / YouTube RSS 实证）：
- 崩坏星穹铁道 B 站官方：mid 1340190821，认证"崩坏：星穹铁道官方账号"（2026-09-05）
- Azur Lane 官方 YouTube：UCPeJaqP6WgxUVDcpYkXZKMQ（RSS 频道名 "Azur Lane Official - Yostar"）
- 崩坏星穹铁道官方 YouTube：UC2PeMPA8PAOp-bynLoCeMLA（RSS 频道名 "Honkai: Star Rail"）
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (platform, target_type, target_value, display_name, co_terms, min_views, tier, community)
_NEW_TARGETS = [
    # 碧蓝航线补齐
    ("bilibili", "keyword_search", "碧蓝航线", "B站搜索·碧蓝航线社区", "[]", 5000, "general", "blhx"),
    ("youtube", "channel_rss", "UCPeJaqP6WgxUVDcpYkXZKMQ", "碧蓝航线 YouTube 官方", "[]", 0, "general", "blhx"),
    ("youtube", "channel_rss", "UCX42emUG96mELubKENIvv-Q", "Rexlent · 社区（碧蓝航线）", '["碧蓝航线"]', 0, "general", "blhx"),
    ("youtube", "channel_rss", "UCnRqDZs2TrRxqPAnafCJGaA", "Xlice · 社区（碧蓝航线）", '["碧蓝航线"]', 0, "general", "blhx"),
    # 崩坏星穹铁道分区
    ("bilibili", "account_dynamics", "1340190821", "崩坏星穹铁道官方账号动态", "[]", 0, "core", "hsr"),
    ("bilibili", "keyword_search", "崩坏星穹铁道", "B站搜索·崩坏星穹铁道社区", "[]", 10000, "general", "hsr"),
    ("youtube", "channel_rss", "UC2PeMPA8PAOp-bynLoCeMLA", "崩坏星穹铁道 YouTube 官方", "[]", 0, "general", "hsr"),
    ("youtube", "channel_rss", "UCX42emUG96mELubKENIvv-Q", "Rexlent · 社区（星穹铁道）", '["崩坏星穹铁道","星穹铁道","崩坏：星穹铁道"]', 0, "general", "hsr"),
    ("youtube", "channel_rss", "UCnRqDZs2TrRxqPAnafCJGaA", "Xlice · 社区（星穹铁道）", '["崩坏星穹铁道","星穹铁道","崩坏：星穹铁道"]', 0, "general", "hsr"),
]


def upgrade() -> None:
    # 同一频道/关键词可被多个分区各自监控，唯一键必须含 community
    op.drop_constraint("uq_target", "collect_targets", type_="unique")
    op.create_unique_constraint(
        "uq_target_community",
        "collect_targets",
        ["platform", "target_type", "target_value", "community"],
    )
    # 历史遗留：碧蓝航线搜索目标曾挂在 wuwa 分区（0024 已把其内容划入 blhx），归位
    op.execute(
        "UPDATE collect_targets SET community='blhx', tier='general' "
        "WHERE community='wuwa' AND platform='bilibili' AND target_type='keyword_search' "
        "AND target_value='碧蓝航线'"
    )
    # 同键目标已存在时跳过（INSERT..SELECT..WHERE NOT EXISTS 兼容 MySQL/SQLite）
    for platform, target_type, target_value, name, co_terms, min_views, tier, community in _NEW_TARGETS:
        op.execute(
            "INSERT INTO collect_targets "
            "(platform, target_type, target_value, display_name, interval_minutes, enabled, "
            " co_terms, min_views, tier, community, created_at, updated_at) "
            f"SELECT '{platform}', '{target_type}', '{target_value}', '{name}', 30, 1, "
            f" '{co_terms}', {min_views}, '{tier}', '{community}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM collect_targets WHERE platform='"
            + platform + "' AND target_type='" + target_type + "' AND target_value='" + target_value
            + "' AND community='" + community + "')"
        )


def downgrade() -> None:
    for platform, target_type, target_value, name, _co, _mv, _tier, community in _NEW_TARGETS:
        op.execute(
            "DELETE FROM collect_targets WHERE platform='" + platform
            + "' AND target_type='" + target_type
            + "' AND target_value='" + target_value
            + "' AND community='" + community + "'"
        )
    op.drop_constraint("uq_target_community", "collect_targets", type_="unique")
    op.create_unique_constraint(
        "uq_target", "collect_targets", ["platform", "target_type", "target_value"]
    )
