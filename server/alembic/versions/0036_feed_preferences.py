"""个人关注流与邮件通知的游戏范围、标题排除词。"""

from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("feed_preferences", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("users", "feed_preferences")
