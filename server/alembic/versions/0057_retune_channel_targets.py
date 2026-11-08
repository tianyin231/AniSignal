"""按频道实际内容调整采集目标：补上 Xlice 的原神、终末地分区，停用长期没有匹配的目标。"""

from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None

XLICE = "UCnRqDZs2TrRxqPAnafCJGaA"
REXLENT = "UCX42emUG96mELubKENIvv-Q"

# 2026-11-08 核对频道 RSS：Xlice 近一个月主要发原神、终末地与鸣潮。
NEW_TARGETS = [
    ("genshin", XLICE, "Xlice · 社区（原神）", ["原神", "Genshin Impact", "Genshin"]),
    ("endfield", XLICE, "Xlice · 社区（终末地）", ["终末地", "Arknights Endfield", "Endfield"]),
]

# 同日核对：Rexlent 只发鸣潮和其他游戏；Xlice 从未发过碧蓝航线和明日方舟本体；
# 帕姆的收藏夹最近 20 条都是抽奖、宣传和同人展示，14 天里没有一条周边公告。
RETIRED = [
    ("youtube", "channel_rss", REXLENT, "blhx"),
    ("youtube", "channel_rss", REXLENT, "hsr"),
    ("youtube", "channel_rss", REXLENT, "arknights"),
    ("youtube", "channel_rss", XLICE, "blhx"),
    ("youtube", "channel_rss", XLICE, "arknights"),
    ("bilibili", "account_dynamics", "508103429", "hsr"),
]


def table():
    return sa.table("collect_targets", sa.column("id", sa.Integer), sa.column("platform", sa.String),
        sa.column("target_type", sa.String), sa.column("target_value", sa.String), sa.column("display_name", sa.String),
        sa.column("community", sa.String), sa.column("interval_minutes", sa.Integer), sa.column("enabled", sa.Boolean),
        sa.column("co_terms", sa.JSON), sa.column("config", sa.JSON), sa.column("min_views", sa.Integer),
        sa.column("consecutive_failures", sa.Integer), sa.column("next_run_at", sa.DateTime),
        sa.column("tier", sa.String), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))


def _key(targets, platform, kind, value, community):
    return ((targets.c.platform == platform) & (targets.c.target_type == kind)
            & (targets.c.target_value == value) & (targets.c.community == community))


def upgrade():
    bind, targets = op.get_bind(), table()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for platform, kind, value, community in RETIRED:
        row = bind.execute(sa.select(targets.c.id, targets.c.enabled, targets.c.config)
                           .where(_key(targets, platform, kind, value, community))).first()
        if row is None or not row.enabled:
            continue  # 管理员已经停用的不做标记，回退时也不会被重新打开。
        bind.execute(targets.update().where(targets.c.id == row.id).values(
            enabled=False, config={**(row.config or {}), "retired_by": revision}, updated_at=now))
    for index, (community, value, name, terms) in enumerate(NEW_TARGETS):
        if bind.execute(sa.select(targets.c.id).where(_key(targets, "youtube", "channel_rss", value, community))).first():
            continue  # 保留管理员已有的开关、频率和过滤条件。
        bind.execute(targets.insert().values(community=community, platform="youtube", target_type="channel_rss",
            target_value=value, display_name=name, interval_minutes=60, min_views=0, tier="general",
            enabled=True, co_terms=terms, config={"seed": revision}, consecutive_failures=0,
            next_run_at=now + timedelta(minutes=index + 1),
            created_at=now, updated_at=now))


def downgrade():
    bind, targets = op.get_bind(), table()
    for row in bind.execute(sa.select(targets.c.id, targets.c.config)).all():
        config = dict(row.config or {})
        if config.get("retired_by") == revision:
            config.pop("retired_by")
            bind.execute(targets.update().where(targets.c.id == row.id).values(enabled=True, config=config))
        elif config.get("seed") == revision:
            bind.execute(targets.update().where(targets.c.id == row.id).values(enabled=False))
