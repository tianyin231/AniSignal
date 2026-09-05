"""product_signals community column (分区冗余列，信号跟随商品分区)

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_signals",
        sa.Column("community", sa.String(length=16), nullable=False, server_default="wuwa"),
    )
    # 存量信号回填为所属商品的分区（此后由 sync_product_signals 强制保持一致）；
    # EXISTS 守卫避免商品行缺失（理论不可能，FK 保护）时回填 NULL 违反非空约束
    op.execute(
        "UPDATE product_signals SET community = "
        "(SELECT community FROM products WHERE products.id = product_signals.product_id) "
        "WHERE community = 'wuwa' AND EXISTS ("
        "SELECT 1 FROM products WHERE products.id = product_signals.product_id)"
    )
    op.create_index("ix_product_signals_community", "product_signals", ["community"])


def downgrade() -> None:
    op.drop_index("ix_product_signals_community", table_name="product_signals")
    op.drop_column("product_signals", "community")
