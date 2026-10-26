"""增加原神、终末地的官方与社区采集目标。"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

# B站认证已核验；终末地 YouTube 使用官网给出的 EN 频道，不使用同名占位账号。
TARGETS = [
    ("genshin", "bilibili", "account_dynamics", "401742377", "原神官方账号动态", 30, 0, "core"),
    ("genshin", "bilibili", "keyword_search", "原神", "B站搜索·原神社区", 60, 10000, "general"),
    ("genshin", "game_official", "notices", "genshin", "原神官网公告", 30, 0, "core"),
    ("genshin", "youtube", "channel_rss", "UCiS882YPwZt1NfaM0gR0D9Q", "原神 YouTube 官方", 60, 0, "core"),
    ("genshin", "x", "account_dynamics", "genshinimpact", "原神 X 官方", 120, 0, "core"),
    ("genshin", "miyoushe", "keyword_search", "原神", "米游社·原神社区", 120, 0, "general"),
    ("endfield", "bilibili", "account_dynamics", "1265652806", "终末地官方账号动态", 30, 0, "core"),
    ("endfield", "bilibili", "keyword_search", "终末地", "B站搜索·终末地社区", 60, 10000, "general"),
    ("endfield", "game_official", "notices", "endfield", "终末地官网公告", 30, 0, "core"),
    ("endfield", "youtube", "channel_rss", "UCowPaVRBzg8CE6K4CB6LJfw", "终末地 YouTube 官方", 60, 0, "core"),
    ("endfield", "x", "account_dynamics", "akendfield", "终末地 X 官方", 120, 0, "core"),
]


def table():
    return sa.table("collect_targets", sa.column("id", sa.Integer), sa.column("platform", sa.String),
        sa.column("target_type", sa.String), sa.column("target_value", sa.String), sa.column("display_name", sa.String),
        sa.column("community", sa.String), sa.column("interval_minutes", sa.Integer), sa.column("enabled", sa.Boolean),
        sa.column("co_terms", sa.JSON), sa.column("config", sa.JSON), sa.column("min_views", sa.Integer),
        sa.column("consecutive_failures", sa.Integer),
        sa.column("tier", sa.String), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))


def upgrade():
    bind, targets = op.get_bind(), table()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for community, platform, kind, value, name, interval, views, tier in TARGETS:
        existing = bind.execute(sa.select(targets.c.id).where(targets.c.platform == platform,
            targets.c.target_type == kind, targets.c.target_value == value)).first()
        if existing:
            continue  # 保留管理员已配置的目标、频率和过滤条件。
        bind.execute(targets.insert().values(community=community, platform=platform, target_type=kind,
            target_value=value, display_name=name, interval_minutes=interval, min_views=views, tier=tier,
            enabled=True, co_terms=[], config={"seed": "0047"}, consecutive_failures=0, created_at=now, updated_at=now))


def downgrade():
    # 回退只停用本次种子目标，保留采集记录与后续人工修改，避免删掉运行历史。
    targets = table()
    op.get_bind().execute(targets.update().where(targets.c.config["seed"].as_string() == "0047").values(enabled=False))
