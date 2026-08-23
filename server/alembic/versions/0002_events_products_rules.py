"""events, evidences, event_revisions, products, classification_rules

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_KW = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "classification_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("pattern", sa.String(length=64), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_classification_rules"),
        **MYSQL_KW,
    )
    op.create_index("ix_classification_rules_category", "classification_rules", ["category"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("skus", sa.JSON(), nullable=True),
        sa.Column("price_note", sa.String(length=64), nullable=True),
        sa.Column("presale_at", sa.String(length=64), nullable=True),
        sa.Column("balance_window", sa.String(length=64), nullable=True),
        sa.Column("original_ship_at", sa.String(length=64), nullable=True),
        sa.Column("ship_note", sa.Text(), nullable=True),
        sa.Column("watch_terms", sa.JSON(), nullable=True),
        sa.Column("stage_hint", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("computed_stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("slug", name="uq_products_slug"),
        **MYSQL_KW,
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("identity", sa.String(length=16), nullable=False, server_default="official"),
        sa.Column("importance", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("confidence", sa.String(length=8), nullable=False, server_default="medium"),
        sa.Column("confidence_reason", sa.Text(), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("status_label", sa.String(length=64), nullable=True),
        sa.Column("regions", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("manual_locked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="fk_events_product_id_products", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        **MYSQL_KW,
    )
    op.create_index("ix_events_category", "events", ["category"])
    op.create_index("ix_events_product_id", "events", ["product_id"])
    op.create_index("ix_events_active_updated", "events", ["is_active", "last_updated_at"])

    op.create_table(
        "evidences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="support"),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_evidences_event_id_events", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["raw_content_id"],
            ["raw_contents.id"],
            name="fk_evidences_raw_content_id_raw_contents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidences"),
        sa.UniqueConstraint("event_id", "raw_content_id", name="uq_evidence"),
        **MYSQL_KW,
    )
    op.create_index("ix_evidences_event_id", "evidences", ["event_id"])
    op.create_index("ix_evidences_raw_content_id", "evidences", ["raw_content_id"])

    op.create_table(
        "event_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False, server_default="engine"),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_event_revisions_event_id_events", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_revisions"),
        **MYSQL_KW,
    )
    op.create_index("ix_event_revisions_event_id", "event_revisions", ["event_id"])

    op.add_column("content_versions", sa.Column("category", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("content_versions", "category")
    op.drop_table("event_revisions")
    op.drop_table("evidences")
    op.drop_table("events")
    op.drop_table("products")
    op.drop_index("ix_classification_rules_category", table_name="classification_rules")
    op.drop_table("classification_rules")
