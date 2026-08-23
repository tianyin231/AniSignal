"""账号模型：用户、邮箱验证码、服务端会话。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.clock import utcnow
from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    # 昵称与头像（头像为预设样式库的 key，如 "aurora-blue"）
    display_name: Mapped[str | None] = mapped_column(String(64))
    avatar: Mapped[str | None] = mapped_column(String(32))
    # 格式：scrypt$n$r$p$salt_hex$hash_hex
    password_hash: Mapped[str] = mapped_column(String(255))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/banned
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class EmailCode(Base):
    __tablename__ = "email_codes"
    __table_args__ = (
        Index("ix_email_codes_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_email_codes_user_id_users", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(String(16))  # register / reset
    code_hash: Mapped[str] = mapped_column(String(64))
    # SMTP 未配置时的开发/排查通道：明码仅在发信失败时保留，管理端凭 Token 查看
    code_plain: Mapped[str | None] = mapped_column(String(8))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserKeyword(Base):
    """用户监控关键词（免费版关注流；通知上线后即监控位）。"""

    __tablename__ = "user_keywords"
    __table_args__ = (
        UniqueConstraint("user_id", "keyword", name="uq_user_keyword"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_user_keywords_user_id_users", ondelete="CASCADE"),
        index=True,
    )
    keyword: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_session_token"),
        Index("ix_user_sessions_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_user_sessions_user_id_users", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")
