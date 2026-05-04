from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from .config import get_settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DevinAccount(Base):
    """A Devin account is essentially one API key the user has added.

    The user can add multiple accounts and the website acts as a single
    front-end that aggregates them.
    """

    __tablename__ = "devin_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    chats: Mapped[list["Chat"]] = relationship(back_populates="account")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), default="New chat", nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("devin_accounts.id", ondelete="CASCADE"), nullable=False
    )
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    account: Mapped[DevinAccount] = relationship(back_populates="chats")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    devin_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    devin_event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    chat: Mapped[Chat] = relationship(back_populates="messages")


_engine = None
_SessionLocal = None


def init_engine() -> None:
    global _engine, _SessionLocal
    settings = get_settings()
    _engine = create_engine(
        settings.db_url, connect_args={"check_same_thread": False}, future=True
    )
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(_engine)


def get_db() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_default_account(db: Session) -> DevinAccount | None:
    """Return the default account, or the first account, or None if none exist."""
    stmt = select(DevinAccount).where(DevinAccount.is_default.is_(True)).limit(1)
    acc = db.execute(stmt).scalar_one_or_none()
    if acc is not None:
        return acc
    stmt = select(DevinAccount).order_by(DevinAccount.id.asc()).limit(1)
    return db.execute(stmt).scalar_one_or_none()
