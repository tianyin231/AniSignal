"""在定制化板块播种站点维护的 NIKKE 国际服官网公告。"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def _owner_nullable(nullable):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    current_fk = next(fk["name"] for fk in inspector.get_foreign_keys("custom_feeds") if fk["constrained_columns"] == ["user_id"])
    target_fk = "fk_custom_feeds_owner_users" if nullable else "fk_custom_feeds_user_id_users"
    column = next(col for col in inspector.get_columns("custom_feeds") if col["name"] == "user_id")
    if column["nullable"] == nullable and current_fk == target_fk:
        return
    if bind.dialect.name == "mysql":
        # 一条原子 DDL 完成外键与空值约束变更，重启不会停在已删外键的中间状态。
        constraint = "NULL" if nullable else "NOT NULL"
        op.execute(sa.text(f"ALTER TABLE custom_feeds DROP FOREIGN KEY {current_fk}, "
            f"MODIFY COLUMN user_id INTEGER {constraint}, "
            f"ADD CONSTRAINT {target_fk} FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE"))
        return
    with op.batch_alter_table("custom_feeds") as batch:
        batch.drop_constraint(current_fk, type_="foreignkey")
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=nullable)
        batch.create_foreign_key(target_fk, "users", ["user_id"], ["id"], ondelete="CASCADE")


def upgrade():
    _owner_nullable(True)
    bind = op.get_bind()
    meta = sa.MetaData()
    sources = sa.Table("custom_feed_sources", meta, autoload_with=bind)
    feeds = sa.Table("custom_feeds", meta, autoload_with=bind)
    scope = (sources.c.platform == "game_official", sources.c.kind == "notices", sources.c.target_value == "nikke_global")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    source_id = bind.scalar(sa.select(sources.c.id).where(*scope))
    if source_id is None:
        source_id = bind.execute(sources.insert().values(platform="game_official", kind="notices", target_value="nikke_global",
            next_run_at=now, consecutive_failures=0, created_at=now)).inserted_primary_key[0]
    existing = bind.scalar(sa.select(feeds.c.id).where(feeds.c.user_id.is_(None), feeds.c.source_id == source_id))
    if existing is None:
        bind.execute(feeds.insert().values(user_id=None, source_id=source_id, name="NIKKE 国际服", kind="notices",
            platform="game_official", target_value="nikke_global", filters={}, min_views=0, push_enabled=False,
            is_public=True, enabled=True, status="active", interval_minutes=30, next_run_at=now,
            created_at=now, updated_at=now))


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT COUNT(*) FROM custom_feeds WHERE user_id IS NULL")):
        raise RuntimeError("请先为站点维护的信息源指定所有者后降级，避免删除已有公告记录")
    _owner_nullable(False)
