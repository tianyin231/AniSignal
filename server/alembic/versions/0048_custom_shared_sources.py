"""定制监控共用公开目标；保留订阅、个人条目与投递历史。"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("custom_feed_sources",
        sa.Column("id", sa.Integer, primary_key=True), sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("target_value", sa.String(128), nullable=False),
        sa.Column("next_run_at", sa.DateTime), sa.Column("lease_until", sa.DateTime),
        sa.Column("last_success_at", sa.DateTime), sa.Column("consecutive_failures", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text), sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("platform", "kind", "target_value", name="uq_custom_source"))
    op.create_table("custom_source_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("custom_feed_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False), sa.Column("normalized", sa.JSON, nullable=False),
        sa.Column("collected_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_custom_source_item"))
    op.create_index("ix_custom_source_items_source_id", "custom_source_items", ["source_id"])
    op.create_index("ix_custom_source_items_collected_at", "custom_source_items", ["collected_at"])
    op.create_table("custom_collect_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("custom_feed_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False), sa.Column("finished_at", sa.DateTime),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("items_found", sa.Integer, nullable=False),
        sa.Column("items_delivered", sa.Integer, nullable=False), sa.Column("error", sa.Text))
    op.create_index("ix_custom_run_time", "custom_collect_runs", ["started_at"])
    op.create_index("ix_custom_collect_runs_source_id", "custom_collect_runs", ["source_id"])
    with op.batch_alter_table("custom_feeds") as batch:
        batch.add_column(sa.Column("source_id", sa.Integer))
        batch.add_column(sa.Column("filters", sa.JSON, nullable=True))
        batch.create_foreign_key("fk_custom_feed_source", "custom_feed_sources", ["source_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_custom_feeds_source_id", ["source_id"])
    bind = op.get_bind()
    meta = sa.MetaData()
    feeds = sa.Table("custom_feeds", meta, autoload_with=bind)
    sources = sa.Table("custom_feed_sources", meta, autoload_with=bind)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    targets = {}
    for feed in bind.execute(sa.select(feeds)).mappings().all():
        target = str(int(feed["target_value"])) if feed["kind"] == "uid" else feed["target_value"].strip().casefold()
        key = (feed["platform"], feed["kind"], target)
        if key not in targets:
            result = bind.execute(sources.insert().values(platform=key[0], kind=key[1], target_value=key[2],
                consecutive_failures=0, created_at=now))
            targets[key] = result.inserted_primary_key[0]
        bind.execute(feeds.update().where(feeds.c.id == feed["id"]).values(source_id=targets[key], filters={}))
    with op.batch_alter_table("custom_feeds") as batch:
        batch.alter_column("filters", existing_type=sa.JSON, nullable=False)


def downgrade():
    with op.batch_alter_table("custom_feeds") as batch:
        batch.drop_constraint("fk_custom_feed_source", type_="foreignkey")
        batch.drop_index("ix_custom_feeds_source_id")
        batch.drop_column("filters")
        batch.drop_column("source_id")
    op.drop_table("custom_collect_runs")
    op.drop_table("custom_source_items")
    op.drop_table("custom_feed_sources")
