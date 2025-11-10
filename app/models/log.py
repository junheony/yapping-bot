"""Log model for audit trail."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class PostLog(Base):
    """Append-only log for processing events."""

    __tablename__ = "post_logs"

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    post: Mapped["PostRecord"] = relationship("PostRecord", backref="logs")

