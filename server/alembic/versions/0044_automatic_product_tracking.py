"""AI 自动发现商品、按内容版本重试，以及商品监控生命周期。"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("expected_ship_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("monitor_started_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("discovery", sa.JSON(), nullable=True))
    with op.batch_alter_table("product_candidates") as batch:
        batch.add_column(sa.Column("details", sa.JSON(), nullable=True))
    with op.batch_alter_table("merch_scans") as batch:
        batch.add_column(sa.Column("content_version", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("result", sa.JSON(), nullable=True))
    op.execute(sa.text("UPDATE products SET monitor_started_at = created_at, discovery = '{}'"))
    op.execute(sa.text("UPDATE product_candidates SET details = '{}'"))
    op.execute(sa.text("UPDATE merch_scans SET result = '{}'"))


def downgrade():
    with op.batch_alter_table("merch_scans") as batch:
        batch.drop_column("result")
        batch.drop_column("content_version")
    with op.batch_alter_table("product_candidates") as batch:
        batch.drop_column("details")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("discovery")
        batch.drop_column("monitor_started_at")
        batch.drop_column("expected_ship_at")
