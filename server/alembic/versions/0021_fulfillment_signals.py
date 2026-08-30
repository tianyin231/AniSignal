"""structured product fulfillment signals

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-30
"""

from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MYSQL_KW = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}

TYPE_MAP = {
    "presale": "presale_open",
    "balance": "balance_open",
    "delay": "delayed",
    "shipping": "shipping_started",
    "arrived": "consumer_received",
    "unboxing": "consumer_received",
    "quality": "quality_issue",
}


def upgrade() -> None:
    op.create_table(
        "product_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("signal_type", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="candidate"),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("source_method", sa.String(length=16), nullable=False, server_default="rule"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"],
            name="fk_product_signals_product_id_products", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_signals"),
        **MYSQL_KW,
    )
    op.create_index("ix_product_signals_product_id", "product_signals", ["product_id"])
    op.create_index(
        "ix_product_signals_product_status", "product_signals", ["product_id", "review_status"]
    )
    op.create_index(
        "ix_product_signals_product_type", "product_signals", ["product_id", "signal_type"]
    )

    op.create_table(
        "product_signal_evidences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("raw_content_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="support"),
        sa.Column("claim_quote", sa.Text(), nullable=True),
        sa.Column("acquisition_type", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("independent_source_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["product_signals.id"],
            name="fk_signal_evidences_signal_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["raw_content_id"], ["raw_contents.id"],
            name="fk_signal_evidences_raw_content_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_signal_evidences"),
        sa.UniqueConstraint("signal_id", "raw_content_id", name="uq_product_signal_evidence"),
        **MYSQL_KW,
    )
    op.create_index("ix_signal_evidences_signal_id", "product_signal_evidences", ["signal_id"])
    op.create_index(
        "ix_signal_evidences_raw_content_id", "product_signal_evidences", ["raw_content_id"]
    )

    op.create_table(
        "product_signal_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False, server_default="admin"),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("next_status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["signal_id"], ["product_signals.id"],
            name="fk_signal_revisions_signal_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_signal_revisions"),
        **MYSQL_KW,
    )
    op.create_index("ix_signal_revisions_signal_id", "product_signal_revisions", ["signal_id"])

    _backfill_existing_evidence()
    _retire_legacy_stage_projection()


def _backfill_existing_evidence() -> None:
    """存量商品证据全部回填；只有明确官方来源自动核验。"""
    bind = op.get_bind()
    metadata = sa.MetaData()
    events = sa.Table("events", metadata, autoload_with=bind)
    links = sa.Table("evidences", metadata, autoload_with=bind)
    raws = sa.Table("raw_contents", metadata, autoload_with=bind)
    sources = sa.Table("sources", metadata, autoload_with=bind)
    signals = sa.Table("product_signals", metadata, autoload_with=bind)
    signal_evidences = sa.Table("product_signal_evidences", metadata, autoload_with=bind)

    rows = bind.execute(
        sa.select(
            events.c.id.label("event_id"),
            events.c.product_id,
            events.c.category,
            events.c.title,
            raws.c.id.label("raw_id"),
            raws.c.platform,
            raws.c.external_id,
            raws.c.published_at,
            raws.c.collected_at,
            sources.c.identity,
            sources.c.external_id.label("source_external_id"),
        )
        .select_from(events)
        .join(links, links.c.event_id == events.c.id)
        .join(raws, raws.c.id == links.c.raw_content_id)
        .outerjoin(sources, sources.c.id == raws.c.source_id)
        .where(
            events.c.product_id.is_not(None),
            events.c.is_active.is_(True),
            links.c.role == "support",
            events.c.category.in_(tuple(TYPE_MAP)),
        )
    )
    now = datetime.utcnow()
    for row in rows.mappings():
        actor = row["identity"] or "unknown"
        is_official = actor == "official" and row["category"] in {
            "presale", "balance", "delay", "shipping"
        }
        result = bind.execute(
            signals.insert().values(
                product_id=row["product_id"],
                signal_type=TYPE_MAP[row["category"]],
                actor_type=actor,
                review_status="verified" if is_official else "candidate",
                occurred_at=row["published_at"] or row["collected_at"],
                summary=row["title"],
                confidence=1.0 if is_official else 0.5,
                details={"legacy_event_id": row["event_id"], "legacy_category": row["category"]},
                source_method="rule",
                active=True,
                locked=False,
                created_at=now,
                updated_at=now,
            )
        )
        signal_id = result.inserted_primary_key[0]
        source_key = (
            f"{row['platform']}:{row['source_external_id']}"
            if row["source_external_id"]
            else f"{row['platform']}:content:{row['external_id']}"
        )
        bind.execute(
            signal_evidences.insert().values(
                signal_id=signal_id,
                raw_content_id=row["raw_id"],
                role="support",
                claim_quote=row["title"],
                acquisition_type="seeded" if actor == "seeded" else "unknown",
                independent_source_key=source_key,
                created_at=now,
            )
        )


def _retire_legacy_stage_projection() -> None:
    """旧阶段字段保留审计，但非人工事件不再公开旧阶段结论。"""
    bind = op.get_bind()
    metadata = sa.MetaData()
    events = sa.Table("events", metadata, autoload_with=bind)
    bind.execute(
        events.update()
        .where(
            events.c.product_id.is_not(None),
            events.c.category.in_(("unboxing", "arrived")),
            events.c.manual_locked.is_(False),
        )
        .values(
            status_label=None,
            confidence="low",
            confidence_reason="到货内容仅作为候选信号，需逐条人工核验",
            conclusion="发现到货/开箱候选内容，尚未形成消费者履约结论",
            facts=[],
        )
    )


def downgrade() -> None:
    op.drop_index("ix_signal_revisions_signal_id", table_name="product_signal_revisions")
    op.drop_table("product_signal_revisions")
    op.drop_index("ix_signal_evidences_raw_content_id", table_name="product_signal_evidences")
    op.drop_index("ix_signal_evidences_signal_id", table_name="product_signal_evidences")
    op.drop_table("product_signal_evidences")
    op.drop_index("ix_product_signals_product_type", table_name="product_signals")
    op.drop_index("ix_product_signals_product_status", table_name="product_signals")
    op.drop_index("ix_product_signals_product_id", table_name="product_signals")
    op.drop_table("product_signals")
