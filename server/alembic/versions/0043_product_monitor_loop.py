"""商品监控窗口、周边候选，以及已结束的爱弥斯键盘归档。"""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("monitor_status", sa.String(16), nullable=False, server_default="watching"))
        batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(512), nullable=True))
    op.create_index("ix_products_monitor_status", "products", ["monitor_status"])
    op.create_table(
        "product_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("community", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("watch_terms", sa.JSON(), nullable=False),
        sa.Column("search_keyword", sa.String(64), nullable=False),
        sa.Column("price_note", sa.String(64), nullable=True),
        sa.Column("presale_note", sa.String(64), nullable=True),
        sa.Column("ship_note", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("raw_content_id", sa.Integer(), sa.ForeignKey("raw_contents.id", name="fk_product_candidates_raw_content", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", name="fk_product_candidates_product", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_product_candidates_status", "product_candidates", ["status", "id"])
    op.create_index("ix_product_candidates_community_name", "product_candidates", ["community", "name"])
    op.create_table(
        "merch_scans",
        sa.Column("raw_content_id", sa.Integer(), sa.ForeignKey("raw_contents.id", name="fk_merch_scans_raw_content", ondelete="CASCADE"), primary_key=True),
        sa.Column("scanned_at", sa.DateTime(), nullable=False),
    )
    op.execute(sa.text(
        "UPDATE products SET monitor_status = 'archived', archived_at = UTC_TIMESTAMP(), "
        "stage_hint = -1, stage_hint_source = NULL "
        "WHERE slug = 'aymiss-keyboard'"
    ))
    op.execute(sa.text(
        "UPDATE collect_targets SET enabled = 0 "
        "WHERE id IN (SELECT monitor_target_id FROM products WHERE slug = 'aymiss-keyboard' AND monitor_target_id IS NOT NULL)"
    ))


def downgrade():
    op.drop_table("merch_scans")
    op.drop_index("ix_product_candidates_community_name", table_name="product_candidates")
    op.drop_index("ix_product_candidates_status", table_name="product_candidates")
    op.drop_table("product_candidates")
    op.drop_index("ix_products_monitor_status", table_name="products")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("source_url")
        batch.drop_column("archived_at")
        batch.drop_column("monitor_status")
