"""在定制化板块增加 NIKKE 国际服官方 YouTube 与 X。"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

# 官网社交入口与频道资料相互核验；国际服英文号，不采用国服《新的希望》账号。
TARGETS = [
    ("youtube", "channel_rss", "UCmmgZwsqQRD6PKeo87uq1bA", "NIKKE 国际服 · 官方 YouTube", 60),
    ("x", "account_dynamics", "nikke_en", "NIKKE 国际服 · 官方 X", 120),
]


def upgrade():
    bind, meta = op.get_bind(), sa.MetaData()
    sources = sa.Table("custom_feed_sources", meta, autoload_with=bind)
    feeds = sa.Table("custom_feeds", meta, autoload_with=bind)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for platform, kind, target, name, interval in TARGETS:
        source_id = bind.scalar(sa.select(sources.c.id).where(sources.c.platform == platform,
            sources.c.kind == kind, sources.c.target_value == target))
        if source_id is None:
            source_id = bind.execute(sources.insert().values(platform=platform, kind=kind, target_value=target,
                next_run_at=now, consecutive_failures=0, created_at=now)).inserted_primary_key[0]
        existing = bind.scalar(sa.select(feeds.c.id).where(feeds.c.user_id.is_(None), feeds.c.source_id == source_id))
        if existing is None:
            bind.execute(feeds.insert().values(user_id=None, source_id=source_id, name=name, platform=platform,
                kind=kind, target_value=target, filters={}, min_views=0, push_enabled=False, is_public=True,
                enabled=True, status="active", interval_minutes=interval, next_run_at=now, created_at=now, updated_at=now))


def downgrade():
    feeds = sa.table("custom_feeds", sa.column("user_id"), sa.column("platform"), sa.column("kind"),
                     sa.column("target_value"), sa.column("enabled"))
    for platform, kind, target, _, _ in TARGETS:
        op.get_bind().execute(feeds.update().where(feeds.c.user_id.is_(None), feeds.c.platform == platform,
            feeds.c.kind == kind, feeds.c.target_value == target).values(enabled=False))
