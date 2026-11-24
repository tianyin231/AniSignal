"""Add official content revision notification flags, cursors, and parsed_at index.

Revision ID: 0062
Revises: 0061
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0062"
down_revision: Union[str, None] = "0061"
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


def _has_index(inspector: sa.Inspector | None, table: str, name: str, *, default: bool = False) -> bool:
    if inspector is None:
        return default
    return any(idx["name"] == name for idx in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    if not _has_column(inspector, "users", "revision_notification_enabled"):
        op.add_column(
            "users",
            sa.Column(
                "revision_notification_enabled",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )
    if not _has_column(inspector, "users", "instant_revision_cursor"):
        op.add_column("users", sa.Column("instant_revision_cursor", sa.Integer(), nullable=True))
    if not _has_column(inspector, "users", "webhook_revision_cursor"):
        op.add_column("users", sa.Column("webhook_revision_cursor", sa.Integer(), nullable=True))

    if not _has_column(inspector, "user_webhooks", "include_revisions"):
        op.add_column(
            "user_webhooks",
            sa.Column(
                "include_revisions",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )

    if not _has_index(inspector, "content_versions", "ix_content_versions_parsed_at"):
        op.create_index("ix_content_versions_parsed_at", "content_versions", ["parsed_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    if _has_index(inspector, "content_versions", "ix_content_versions_parsed_at", default=True):
        op.drop_index("ix_content_versions_parsed_at", table_name="content_versions")
    if _has_column(inspector, "user_webhooks", "include_revisions", default=True):
        op.drop_column("user_webhooks", "include_revisions")
    if _has_column(inspector, "users", "webhook_revision_cursor", default=True):
        op.drop_column("users", "webhook_revision_cursor")
    if _has_column(inspector, "users", "instant_revision_cursor", default=True):
        op.drop_column("users", "instant_revision_cursor")
    if _has_column(inspector, "users", "revision_notification_enabled", default=True):
        op.drop_column("users", "revision_notification_enabled")
