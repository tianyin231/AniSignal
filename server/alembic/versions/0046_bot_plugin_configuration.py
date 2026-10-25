"""按 AstrBot 实例隔离会话，由插件维护订阅配置。"""

from alembic import op
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    uniques = sa.inspect(op.get_bind()).get_unique_constraints("bot_targets")
    with op.batch_alter_table("bot_targets", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
        batch.add_column(sa.Column("client_id", sa.String(40), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("managed_by_plugin", sa.Boolean(), nullable=False, server_default=sa.false()))
        for constraint in uniques:
            if constraint["column_names"] == ["umo"]:
                batch.drop_constraint(constraint["name"] or "uq_bot_targets_umo", type_="unique")
        batch.create_unique_constraint("uq_bot_targets_client_umo", ["client_id", "umo"])
        batch.create_index("ix_bot_targets_client_id", ["client_id"])


def downgrade():
    # 同一 UMO 可能已被不同机器人使用；有重名时数据库会拒绝降级，避免丢失会话。
    with op.batch_alter_table("bot_targets") as batch:
        batch.drop_index("ix_bot_targets_client_id")
        batch.drop_constraint("uq_bot_targets_client_umo", type_="unique")
        batch.create_unique_constraint("uq_bot_targets_umo", ["umo"])
        batch.drop_column("managed_by_plugin")
        batch.drop_column("client_id")
