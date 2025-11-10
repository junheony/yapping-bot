"""Research posts captured for analysis."""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ResearchPost(Base):
    """Tweet/post snapshot for qualitative analysis."""

    __tablename__ = "research_posts"

    tweet_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    handle: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
