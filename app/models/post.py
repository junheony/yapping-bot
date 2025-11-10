"""Post persistence model."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PostStatus(str):
    """Simple status labels."""

    PENDING = "pending"
    POSTED = "posted"
    SKIPPED = "skipped"
    ERROR = "error"


class PostRecord(Base):
    """Persisted post metadata for dedupe and auditing."""

    __tablename__ = "posts"

    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    lang: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    text_ko: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    text_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hashtags: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    channels: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        Enum(
            PostStatus.PENDING,
            PostStatus.POSTED,
            PostStatus.SKIPPED,
            PostStatus.ERROR,
            name="post_status",
        ),
        default=PostStatus.PENDING,
        nullable=False,
    )
    manual_review_required: Mapped[bool] = mapped_column(default=False)
    approved: Mapped[bool] = mapped_column(default=True)
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    priority: Mapped[int] = mapped_column(default=0)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    token_usage: Mapped[Optional[int]] = mapped_column(nullable=True)
