"""采集意图打标：目标意图描述、内容三档结论与忽略计数。"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("collect_targets", sa.Column("intent", sa.Text(), nullable=True))
    op.add_column("raw_contents", sa.Column("intent_tier", sa.String(16), nullable=True))
    op.add_column("raw_contents", sa.Column("intent_reason", sa.String(300), nullable=True))
    op.add_column("raw_contents", sa.Column("intent_labeled_at", sa.DateTime(), nullable=True))
    op.create_index("ix_raw_contents_intent_tier", "raw_contents", ["intent_tier"])
    op.add_column("collect_runs", sa.Column("items_ignored", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("collect_runs", "items_ignored")
    op.drop_index("ix_raw_contents_intent_tier", "raw_contents")
    op.drop_column("raw_contents", "intent_labeled_at")
    op.drop_column("raw_contents", "intent_reason")
    op.drop_column("raw_contents", "intent_tier")
    op.drop_column("collect_targets", "intent")
