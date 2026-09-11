"""AstrBot 人格播报与会话提醒。"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bot_deliveries", sa.Column("rendered_body", sa.Text(), nullable=True))
    op.add_column("bot_deliveries", sa.Column("receipt", sa.String(64), nullable=True))
    op.create_table(
        "bot_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("bot_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bot_reminders_target_id", "bot_reminders", ["target_id"])
    op.create_index("ix_bot_reminders_due_at", "bot_reminders", ["due_at"])


def downgrade():
    op.drop_table("bot_reminders")
    op.drop_column("bot_deliveries", "receipt")
    op.drop_column("bot_deliveries", "rendered_body")
