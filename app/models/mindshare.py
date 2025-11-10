"""Mindshare target models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class MindshareSource(str):
    KAITO = "kaito"
    GOMTU_TOP = "gomtu_top_yappers"
    GOMTU_STATS = "gomtu_lb_stats"
    KAITO_SUPABASE = "kaito_supabase"
    KAITO_TARGETS = "kaito_targets"


class MindshareTarget(Base):
    """Represents a target account/project from leaderboard analytics."""

    __tablename__ = "mindshare_targets"

    source: Mapped[str] = mapped_column(
        Enum(
            MindshareSource.KAITO,
            MindshareSource.GOMTU_TOP,
            MindshareSource.GOMTU_STATS,
            MindshareSource.KAITO_SUPABASE,
            MindshareSource.KAITO_TARGETS,
            name="mindshare_source",
        ),
        nullable=False,
    )
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    handle: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    topic: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stats: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inner_circle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
