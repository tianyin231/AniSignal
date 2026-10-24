"""RSS 统一采集配置和旧资讯标识映射。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    # RSS 可能包含完整长文；MySQL TEXT 的 64 KB 不足以无损保存。
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("content_versions", "text", existing_type=sa.Text(), type_=MEDIUMTEXT(), existing_nullable=True)
    op.add_column("collect_targets", sa.Column("config", sa.JSON(), nullable=True))
    op.execute(sa.text("UPDATE collect_targets SET config = '{}' WHERE config IS NULL"))
    with op.batch_alter_table("collect_targets") as batch:
        batch.alter_column("config", existing_type=sa.JSON(), nullable=False)
    op.create_table("news_archives",
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("raw_content_id", sa.Integer(), sa.ForeignKey("raw_contents.id", ondelete="SET NULL")),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="SET NULL")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_news_archives_raw_content_id", "news_archives", ["raw_content_id"])
    op.create_index("ix_news_archives_event_id", "news_archives", ["event_id"])


def downgrade():
    op.drop_table("news_archives")
    op.drop_column("collect_targets", "config")
