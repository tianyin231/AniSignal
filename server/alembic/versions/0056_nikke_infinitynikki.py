"""NIKKE 国际服升级独立分区，增加无限暖暖；历史快照由 promote_nikke 命令导入。"""

from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None

# NIKKE 沿用原定制化的国际服来源；无限暖暖 B站企业认证已于 2026-11-07 核验。
TARGETS = [
    ("nikke", "game_official", "notices", "nikke", "NIKKE 国际服官网公告", 30, 0, "core", "nikke_global", []),
    ("nikke", "youtube", "channel_rss", "UCmmgZwsqQRD6PKeo87uq1bA", "NIKKE 国际服 · 官方 YouTube", 60, 0, "core", "UCmmgZwsqQRD6PKeo87uq1bA", []),
    ("nikke", "x", "account_dynamics", "nikke_en", "NIKKE 国际服 · 官方 X", 120, 0, "core", "nikke_en", []),
    ("infinitynikki", "bilibili", "account_dynamics", "3461576715667734", "无限暖暖·官方动态", 30, 0, "core", None, []),
    ("infinitynikki", "bilibili", "keyword_search", "无限暖暖", "B站搜索·无限暖暖社区", 60, 10000, "general", None, ["无限暖暖", "無限暖暖", "Infinity Nikki"]),
]


def upgrade():
    bind, meta = op.get_bind(), sa.MetaData()
    targets = sa.Table("collect_targets", meta, autoload_with=bind)
    feeds = sa.Table("custom_feeds", meta, autoload_with=bind)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for index, (community, platform, kind, value, name, interval, views, tier, legacy_value, terms) in enumerate(TARGETS):
        legacy = bind.execute(sa.select(feeds).where(feeds.c.user_id.is_(None), feeds.c.platform == platform,
            feeds.c.kind == kind, feeds.c.target_value == legacy_value)).mappings().first() if legacy_value else None
        existing = bind.scalar(sa.select(targets.c.id).where(targets.c.platform == platform,
            targets.c.target_type == kind, targets.c.target_value == value))
        if existing is None:
            config = {"seed": revision}
            if legacy:
                config.update(legacy_feed_id=legacy["id"], legacy_enabled=legacy["enabled"])
            bind.execute(targets.insert().values(community=community, platform=platform, target_type=kind,
                target_value=value, display_name=name, interval_minutes=legacy["interval_minutes"] if legacy else interval,
                enabled=bool(legacy["enabled"] and legacy["status"] == "active") if legacy else True,
                co_terms=terms, min_views=views, tier=tier, config=config, consecutive_failures=0,
                # 留出导入历史快照的时间；上线验收按来源依次触发首轮采集。
                next_run_at=now + timedelta(minutes=30 + index * 2), created_at=now, updated_at=now))
        if legacy:
            bind.execute(feeds.update().where(feeds.c.id == legacy["id"]).values(enabled=False, updated_at=now))


def downgrade():
    bind, meta = op.get_bind(), sa.MetaData()
    targets = sa.Table("collect_targets", meta, autoload_with=bind)
    feeds = sa.Table("custom_feeds", meta, autoload_with=bind)
    for row in bind.execute(sa.select(targets).where(targets.c.config["seed"].as_string() == revision)).mappings():
        if feed_id := row["config"].get("legacy_feed_id"):
            bind.execute(feeds.update().where(feeds.c.id == feed_id).values(enabled=row["config"]["legacy_enabled"]))
    bind.execute(targets.update().where(targets.c.config["seed"].as_string() == revision).values(enabled=False))
