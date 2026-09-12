"""官方内容实体抽取：活动/维护日程与兑换码。"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("raw_contents", sa.Column("extracted_at", sa.DateTime(), nullable=True))
    op.create_table(
        "content_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "raw_content_id",
            sa.Integer(),
            sa.ForeignKeyConstraint(["raw_content_id"], ["raw_contents.id"], ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("community", sa.String(32), nullable=False, server_default="wuwa"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("delayed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("raw_content_id", "title", name="uq_content_schedule"),
    )
    op.create_index(
        "ix_content_schedules_community_start", "content_schedules", ["community", "start_at"]
    )
    op.create_table(
        "redeem_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "raw_content_id",
            sa.Integer(),
            sa.ForeignKeyConstraint(["raw_content_id"], ["raw_contents.id"], ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("community", sa.String(32), nullable=False, server_default="wuwa"),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("community", "code", name="uq_redeem_code"),
    )
    op.create_index("ix_redeem_codes_community_status", "redeem_codes", ["community", "status"])


def downgrade():
    op.drop_table("redeem_codes")
    op.drop_table("content_schedules")
    op.drop_column("raw_contents", "extracted_at")
