"""Models for external API usage tracking."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class APIUsageStat(Base):
    """Aggregated usage window for an external API provider."""

    __tablename__ = "api_usage_stats"
    __table_args__ = (
        UniqueConstraint("provider", "metric", "window_start", "window_end", name="uq_api_usage_window"),
    )

    provider: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def cycle_contains(self, moment: datetime) -> bool:
        """Return True if the provided timestamp falls within this usage window."""

        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return self.window_start <= moment < self.window_end
