"""Models for engagement (likes/replies) workflow."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class EngagementAction(str):
    LIKE = "like"
    REPLY = "reply"


class EngagementStatus(str):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    ERROR = "error"


class EngagementTask(Base):
    """Represents a tweet to engage with."""

    __tablename__ = "engagement_tasks"

    tweet_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    tweet_url: Mapped[str] = mapped_column(String(512), nullable=False)
    author_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    author_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(
        Enum(EngagementAction.LIKE, EngagementAction.REPLY, name="engagement_action"),
        default=EngagementAction.LIKE,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            EngagementStatus.PENDING,
            EngagementStatus.PROCESSING,
            EngagementStatus.COMPLETED,
            EngagementStatus.SKIPPED,
            EngagementStatus.ERROR,
            name="engagement_status",
        ),
        default=EngagementStatus.PENDING,
        nullable=False,
    )
    source_query: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metrics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    response_tweet_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class EngagementLog(Base):
    """Log entry for engagement actions."""

    __tablename__ = "engagement_logs"

    task_id: Mapped[int] = mapped_column(ForeignKey("engagement_tasks.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EngagementOutcome(Base):
    """Post-engagement outcome metrics."""

    __tablename__ = "engagement_outcomes"

    task_id: Mapped[int] = mapped_column(ForeignKey("engagement_tasks.id"), unique=True, nullable=False)
    tweet_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    author_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reply_tweet_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    like_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reply_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    author_following: Mapped[Optional[bool]] = mapped_column(nullable=True)
    replied: Mapped[Optional[bool]] = mapped_column(nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
