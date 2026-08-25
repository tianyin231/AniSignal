"""indexes for publication-time feeds

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_raw_contents_published_at", "raw_contents", ["published_at"])
    op.create_index("ix_sources_identity", "sources", ["identity"])


def downgrade() -> None:
    op.drop_index("ix_sources_identity", table_name="sources")
    op.drop_index("ix_raw_contents_published_at", table_name="raw_contents")
