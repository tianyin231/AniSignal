"""资讯事件归组、调用回执与固定刊期。"""

from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sources", sa.Column("owner_key", sa.String(64), nullable=True))
    op.add_column("sources", sa.Column("editorial_tier", sa.String(16), nullable=True))
    op.create_table("editorial_receipts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response", sa.Text()),
        sa.Column("usage_id", sa.Integer(), sa.ForeignKey("ai_usage.id", ondelete="SET NULL")),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_table("news_groupings",
        sa.Column("raw_content_id", sa.Integer(), sa.ForeignKey("raw_contents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="SET NULL")),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="SET NULL")),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("review_key", sa.String(64), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False),
        sa.Column("related_event_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_news_groupings_story", "news_groupings", ["story_id", "event_id"])
    op.create_table("news_editions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("community", sa.String(16), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("community", "day", name="uq_news_edition_day"))


def downgrade():
    op.drop_table("news_editions")
    op.drop_table("news_groupings")
    op.drop_table("editorial_receipts")
    op.drop_column("sources", "editorial_tier")
    op.drop_column("sources", "owner_key")
