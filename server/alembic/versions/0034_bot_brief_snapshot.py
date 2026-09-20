"""保存播报条目，按原编号展开同一份资料。"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bot_deliveries", sa.Column("brief", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("bot_deliveries", "brief")
