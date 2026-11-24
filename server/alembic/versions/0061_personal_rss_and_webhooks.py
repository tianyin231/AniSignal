"""Add users.feed_token, users.webhook_notification_cursor, and user_webhooks table.

Revision ID: 0061
Revises: 0060
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector(bind: sa.Connection) -> sa.Inspector | None:
    if op.get_context().as_sql:
        return None
    return sa.inspect(bind)


def _has_table(inspector: sa.Inspector | None, table: str, *, default: bool = False) -> bool:
    if inspector is None:
        return default
    return table in inspector.get_table_names()


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

    if not _has_column(inspector, "users", "webhook_notification_cursor"):
        op.add_column("users", sa.Column("webhook_notification_cursor", sa.Integer(), nullable=True))
    if not _has_column(inspector, "users", "feed_token"):
        op.add_column("users", sa.Column("feed_token", sa.String(length=64), nullable=True))
    inspector = _inspector(bind)
    if not _has_index(inspector, "users", "ix_users_feed_token"):
        op.create_index("ix_users_feed_token", "users", ["feed_token"], unique=True)

    if not _has_table(inspector, "user_webhooks"):
        op.create_table(
            "user_webhooks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=24), nullable=False),
            sa.Column("url", sa.String(length=512), nullable=False),
            sa.Column("secret", sa.String(length=256), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("events_mode", sa.String(length=16), nullable=False),
            sa.Column("last_status", sa.String(length=16), nullable=True),
            sa.Column("last_error", sa.String(length=255), nullable=True),
            sa.Column("last_sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name="fk_user_webhooks_user_id_users",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_user_webhooks")),
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_unicode_ci",
        )
        op.create_index(op.f("ix_user_webhooks_user_id"), "user_webhooks", ["user_id"], unique=False)
        op.create_index("ix_user_webhooks_user_enabled", "user_webhooks", ["user_id", "enabled"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = _inspector(bind)

    if _has_table(inspector, "user_webhooks", default=True):
        if _has_index(inspector, "user_webhooks", "ix_user_webhooks_user_enabled", default=True):
            op.drop_index("ix_user_webhooks_user_enabled", table_name="user_webhooks")
        if _has_index(inspector, "user_webhooks", op.f("ix_user_webhooks_user_id"), default=True):
            op.drop_index(op.f("ix_user_webhooks_user_id"), table_name="user_webhooks")
        op.drop_table("user_webhooks")

    if _has_index(inspector, "users", "ix_users_feed_token", default=True):
        op.drop_index("ix_users_feed_token", table_name="users")
    if _has_column(inspector, "users", "feed_token", default=True):
        op.drop_column("users", "feed_token")
    if _has_column(inspector, "users", "webhook_notification_cursor", default=True):
        op.drop_column("users", "webhook_notification_cursor")
