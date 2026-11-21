"""Steam 游戏遥测快照：在线人数、官方好评率与商店价格时序记录。"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import utcnow
from app.models.base import Base


class SteamAppSnapshot(Base):
    """按分区与 Steam AppID 采样的游戏级时序快照（与 RawContent 内容流隔离）。"""

    __tablename__ = "steam_app_snapshots"
    __table_args__ = (
        Index("ix_steam_snapshots_comm_app_time", "community", "app_id", "captured_at"),
        Index("ix_steam_snapshots_captured_at", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    community: Mapped[str] = mapped_column(String(16), index=True)
    app_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    player_count: Mapped[int | None] = mapped_column(Integer)
    global_positive: Mapped[int | None] = mapped_column(Integer)
    global_negative: Mapped[int | None] = mapped_column(Integer)
    global_score_desc: Mapped[str | None] = mapped_column(String(48))
    cn_positive: Mapped[int | None] = mapped_column(Integer)
    cn_negative: Mapped[int | None] = mapped_column(Integer)
    price_formatted: Mapped[str | None] = mapped_column(String(64))
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
