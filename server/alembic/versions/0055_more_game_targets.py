"""增加绝区零等六个游戏的官方动态与社区监控。"""

from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

# 2026-11-07 使用 bili user 核验六个账号的 official_role=3 与企业认证名称。
GAMES = [
    ("zzz", "绝区零", "1636034895", ["绝区零", "絕區零", "Zenless Zone Zero", "ZZZ"]),
    ("petitplanet", "星布谷地", "3546622923377024", ["星布谷地", "Petit Planet"]),
    ("hna", "因缘精灵", "3546886017387331", ["因缘精灵", "因緣精靈", "Nexus Anima"]),
    ("roco", "洛克王国世界", "626796832", ["洛克王国世界", "洛克王国：世界", "洛克王国:世界", "洛克王国 世界", "Roco Kingdom"]),
    ("promilia", "蓝色星原", "3546569016085336", ["蓝色星原", "藍色星原", "Azur Promilia"]),
    ("nte", "异环", "3546636978489848", ["异环", "異環", "Neverness to Everness", "NTE"]),
]


def table():
    return sa.table("collect_targets", sa.column("id", sa.Integer), sa.column("platform", sa.String),
        sa.column("target_type", sa.String), sa.column("target_value", sa.String), sa.column("display_name", sa.String),
        sa.column("community", sa.String), sa.column("interval_minutes", sa.Integer), sa.column("enabled", sa.Boolean),
        sa.column("co_terms", sa.JSON), sa.column("config", sa.JSON), sa.column("min_views", sa.Integer),
        sa.column("consecutive_failures", sa.Integer), sa.column("next_run_at", sa.DateTime),
        sa.column("tier", sa.String), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))


def upgrade():
    bind, targets = op.get_bind(), table()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for index, (community, keyword, mid, aliases) in enumerate(GAMES):
        for kind, value, name, interval, views, tier, terms in [
            ("account_dynamics", mid, f"{keyword}·官方动态", 30, 0, "core", []),
            ("keyword_search", keyword, f"B站搜索·{keyword}社区", 60, 10000, "general", aliases),
        ]:
            existing = bind.execute(sa.select(targets.c.id).where(targets.c.platform == "bilibili",
                targets.c.target_type == kind, targets.c.target_value == value)).first()
            if existing:
                continue  # 保留管理员已有的分区、开关、频率和过滤条件。
            bind.execute(targets.insert().values(community=community, platform="bilibili", target_type=kind,
                target_value=value, display_name=name, interval_minutes=interval, min_views=views, tier=tier,
                enabled=True, co_terms=terms, config={"seed": revision}, consecutive_failures=0,
                # 错峰启动，避免扩容后同一时刻向 B站发起全部请求。
                next_run_at=now + timedelta(minutes=index * 2 + (kind == "keyword_search")),
                created_at=now, updated_at=now))


def downgrade():
    targets = table()
    op.get_bind().execute(targets.update().where(targets.c.config["seed"].as_string() == revision).values(enabled=False))
