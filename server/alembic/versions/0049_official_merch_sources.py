"""增加已核验的游戏周边官方 B站动态来源。"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

# 2026-10-28 bili user 逐个核对 official_role=3、企业认证名称与近期动态。
TARGETS = [
    ("wuwa", "3493082584582776", "库洛游戏周边"),
    ("hsr", "508103429", "帕姆的收藏夹"),
    ("arknights", "3546864272017883", "朝陇山播报站"),
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
    for community, mid, name in TARGETS:
        existing = bind.execute(sa.select(targets.c.id).where(targets.c.platform == "bilibili",
            targets.c.target_type == "account_dynamics", targets.c.target_value == mid)).first()
        if existing:
            continue  # 保留管理员已有的开关、频率和过滤设置。
        bind.execute(targets.insert().values(community=community, platform="bilibili", target_type="account_dynamics",
            target_value=mid, display_name=f"{name}·官方周边动态", interval_minutes=30, min_views=0, tier="core",
            enabled=True, co_terms=[], config={"seed": "0049"}, consecutive_failures=0,
            next_run_at=now, created_at=now, updated_at=now))


def downgrade():
    targets = table()
    op.get_bind().execute(targets.update().where(targets.c.config["seed"].as_string() == "0049").values(enabled=False))
