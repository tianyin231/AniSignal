"""重要变化即时邮件的主动订阅与修订进度。"""

from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("instant_notification_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("instant_notification_cursor", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("users", "instant_notification_cursor")
    op.drop_column("users", "instant_notification_enabled")
