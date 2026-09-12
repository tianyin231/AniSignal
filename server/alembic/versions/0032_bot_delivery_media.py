"""投递附带封面图片，供人格播报随消息发送。"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bot_deliveries", sa.Column("media", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("bot_deliveries", "media")
