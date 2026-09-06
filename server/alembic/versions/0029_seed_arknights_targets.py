"""seed arknights targets (明日方舟分区监视源)

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-06

官方身份核验记录：
- 明日方舟 B 站官方：mid 161775300，认证"明日方舟官方账号"（2026-09-06，751 万粉）
- 明日方舟官方 YouTube（EN）：UCR0J2NYGuC8epsa1O4DMmXQ（handle 页 canonical +
  externalId 双确认，频道名 "Arknights Official - Yostar"；站点采集器请求头可正常拉取 RSS）
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (platform, target_type, target_value, display_name, co_terms, min_views, tier)
_NEW_TARGETS = [
    ("bilibili", "account_dynamics", "161775300", "明日方舟官方账号动态", "[]", 0, "core"),
    ("bilibili", "keyword_search", "明日方舟", "B站搜索·明日方舟社区", "[]", 10000, "general"),
    ("youtube", "channel_rss", "UCR0J2NYGuC8epsa1O4DMmXQ", "明日方舟 YouTube 官方", "[]", 0, "general"),
    ("youtube", "channel_rss", "UCX42emUG96mELubKENIvv-Q", "Rexlent · 社区（明日方舟）", '["明日方舟"]', 0, "general"),
    ("youtube", "channel_rss", "UCnRqDZs2TrRxqPAnafCJGaA", "Xlice · 社区（明日方舟）", '["明日方舟"]', 0, "general"),
]


def upgrade() -> None:
    for platform, target_type, target_value, name, co_terms, min_views, tier in _NEW_TARGETS:
        op.execute(
            "INSERT INTO collect_targets "
            "(platform, target_type, target_value, display_name, interval_minutes, enabled, "
            " co_terms, min_views, tier, community, created_at, updated_at) "
            f"SELECT '{platform}', '{target_type}', '{target_value}', '{name}', 30, 1, "
            f" '{co_terms}', {min_views}, '{tier}', 'arknights', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM collect_targets WHERE platform='"
            + platform + "' AND target_type='" + target_type + "' AND target_value='" + target_value
            + "' AND community='arknights')"
        )


def downgrade() -> None:
    for platform, target_type, target_value, _name, _co, _mv, _tier in _NEW_TARGETS:
        op.execute(
            "DELETE FROM collect_targets WHERE platform='" + platform
            + "' AND target_type='" + target_type
            + "' AND target_value='" + target_value
            + "' AND community='arknights'"
        )
