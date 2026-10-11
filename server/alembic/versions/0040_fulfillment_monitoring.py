"""商品专属采集、履约更新时间与核验通知游标。"""

from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("monitor_target_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("fulfillment_updated_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key("fk_products_monitor_target", "collect_targets", ["monitor_target_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_products_monitor_target_id", ["monitor_target_id"])
    op.add_column("users", sa.Column("instant_fulfillment_cursor", sa.Integer(), nullable=True))
    op.create_table(
        "product_fulfillment_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", name="fk_product_fulfillment_revisions_product", ondelete="CASCADE"), nullable=False),
        sa.Column("community", sa.String(16), nullable=False),
        sa.Column("previous_snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_product_fulfillment_revisions_product_id", "product_fulfillment_revisions", ["product_id", "id"])
    op.execute(sa.text("UPDATE users SET instant_fulfillment_cursor = 0"))
    op.execute(sa.text("UPDATE products SET fulfillment_updated_at = (SELECT MAX(updated_at) FROM product_signals WHERE product_signals.product_id = products.id)"))


def downgrade():
    op.drop_table("product_fulfillment_revisions")
    op.drop_column("users", "instant_fulfillment_cursor")
    with op.batch_alter_table("products") as batch:
        batch.drop_constraint("fk_products_monitor_target", type_="foreignkey")
        batch.drop_constraint("uq_products_monitor_target_id", type_="unique")
        batch.drop_column("fulfillment_updated_at")
        batch.drop_column("monitor_target_id")
