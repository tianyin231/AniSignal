"""Enrich redeem_codes with source credibility, server, reward, and estimated expiry flags.

Revision ID: 0063
Revises: 0062
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector(bind: sa.Connection) -> sa.Inspector | None:
    if op.get_context().as_sql:
        return None
    return sa.inspect(bind)


def _has_column(inspector: sa.Inspector | None, table: str, column: str, *, default: bool = False) -> bool:
    if inspector is None:
        return default
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    if not _has_column(inspector, "redeem_codes", "source_kind"):
        op.add_column(
            "redeem_codes",
            sa.Column("source_kind", sa.String(16), nullable=False, server_default="official"),
        )
    if not _has_column(inspector, "redeem_codes", "source_count"):
        op.add_column(
            "redeem_codes",
            sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column(inspector, "redeem_codes", "server"):
        op.add_column("redeem_codes", sa.Column("server", sa.String(16), nullable=True))
    if not _has_column(inspector, "redeem_codes", "reward"):
        op.add_column("redeem_codes", sa.Column("reward", sa.String(120), nullable=True))
    if not _has_column(inspector, "redeem_codes", "expiry_estimated"):
        op.add_column(
            "redeem_codes",
            sa.Column("expiry_estimated", sa.Boolean(), nullable=False, server_default="0"),
        )
    if not _has_column(inspector, "redeem_codes", "confirmed_sources"):
        op.add_column("redeem_codes", sa.Column("confirmed_sources", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    for col in ("confirmed_sources", "expiry_estimated", "reward", "server", "source_count", "source_kind"):
        if _has_column(inspector, "redeem_codes", col, default=True):
            op.drop_column("redeem_codes", col)
