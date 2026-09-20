"""保留公告只有日期、没有具体时刻的精度。"""

from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("content_schedules", sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("content_schedules", "all_day")
