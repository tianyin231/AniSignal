"""指标窗口查询与最新快照索引。"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_metric_snapshots_time_content", "metric_snapshots", ["captured_at", "raw_content_id"])
    op.create_index("ix_metric_snapshots_content_id", "metric_snapshots", ["raw_content_id", "id"])
    op.create_index("ix_ai_usage_retry", "ai_usage", ["task", "ref_id", "status", "created_at"])


def downgrade():
    op.drop_index("ix_ai_usage_retry", table_name="ai_usage")
    op.drop_index("ix_metric_snapshots_content_id", table_name="metric_snapshots")
    op.drop_index("ix_metric_snapshots_time_content", table_name="metric_snapshots")
