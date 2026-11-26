"""Widen custom_feeds/custom_feed_sources target_value to 255 for web_page and RSS URLs.

Revision ID: 0064
Revises: 0063
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _alter_target_value(table: str, old_len: int, new_len: int) -> None:
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "target_value",
                existing_type=sa.String(old_len),
                type_=sa.String(new_len),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            table,
            "target_value",
            existing_type=sa.String(old_len),
            type_=sa.String(new_len),
            existing_nullable=False,
        )


def upgrade() -> None:
    _alter_target_value("custom_feeds", 128, 255)
    _alter_target_value("custom_feed_sources", 128, 255)


def downgrade() -> None:
    _alter_target_value("custom_feed_sources", 255, 128)
    _alter_target_value("custom_feeds", 255, 128)
