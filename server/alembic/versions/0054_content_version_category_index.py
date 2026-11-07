"""公开列表计数只用版本和分类，覆盖索引让它不必读正文和 JSON 所在的宽行。"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_content_versions_latest_category", "content_versions", ["raw_content_id", "version", "category"],
    )


def downgrade():
    op.drop_index("ix_content_versions_latest_category", table_name="content_versions")
