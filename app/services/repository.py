"""Database repository helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PostLog, PostRecord, PostStatus


class PostRepository:
    """Read/write helper for post records."""

    @staticmethod
    async def get_by_hash(session: AsyncSession, hash_value: str) -> Optional[PostRecord]:
        stmt = select(PostRecord).where(PostRecord.hash == hash_value)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        hash_value: str,
        title: str,
        url: str,
        source: Optional[str],
        source_type: Optional[str],
        lang: Optional[str],
        tags: Optional[str],
        prompt_version: Optional[str],
        manual_review_required: bool,
        approved: bool,
        queued_at: Optional[datetime],
        priority: int,
    ) -> PostRecord:
        record = PostRecord(
            hash=hash_value,
            title=title,
            url=url,
            source=source,
            source_type=source_type,
            lang=lang,
            tags=tags,
            prompt_version=prompt_version,
            status=PostStatus.PENDING,
            manual_review_required=manual_review_required,
            approved=approved,
            queued_at=queued_at,
            next_attempt_at=queued_at,
            priority=priority,
        )
        session.add(record)
        await session.flush()
        return record

    @staticmethod
    async def update_with_generation(
        session: AsyncSession,
        record: PostRecord,
        *,
        text_ko: str,
        text_en: str,
        hashtags: str,
        token_usage: Optional[int],
    ) -> None:
        record.text_ko = text_ko
        record.text_en = text_en
        record.hashtags = hashtags
        record.token_usage = token_usage
        await session.flush()

    @staticmethod
    async def mark_status(
        session: AsyncSession,
        record: PostRecord,
        *,
        status: str,
        channels: Optional[str] = None,
        posted_at: Optional[datetime] = None,
        error_msg: Optional[str] = None,
        next_attempt_at: Optional[datetime] = None,
        increment_attempt: bool = False,
        clear_next_attempt: bool = False,
    ) -> None:
        record.status = status
        if channels is not None:
            record.channels = channels
        record.posted_at = posted_at
        record.error_msg = error_msg
        if next_attempt_at is not None:
            record.next_attempt_at = next_attempt_at
        elif clear_next_attempt:
            record.next_attempt_at = None
        if increment_attempt:
            record.attempt_count += 1
        await session.flush()

    @staticmethod
    async def get_pending_queue(
        session: AsyncSession,
        *,
        limit: int,
        now: datetime,
        require_manual: Optional[bool] = None,
    ) -> list[PostRecord]:
        """Return posts ready for processing."""

        stmt = (
            select(PostRecord)
            .where(PostRecord.status == PostStatus.PENDING)
            .where(PostRecord.approved.is_(True))
            .where((PostRecord.next_attempt_at.is_(None)) | (PostRecord.next_attempt_at <= now))
            .order_by(PostRecord.priority.desc(), PostRecord.queued_at.asc())
            .limit(limit)
        )

        if require_manual is not None:
            stmt = stmt.where(PostRecord.manual_review_required.is_(require_manual))

        result = await session.execute(stmt)
        return list(result.scalars())

    @staticmethod
    async def approve_post(session: AsyncSession, record: PostRecord) -> None:
        record.approved = True
        await session.flush()

    @staticmethod
    async def set_manual_review(session: AsyncSession, record: PostRecord, required: bool) -> None:
        record.manual_review_required = required
        await session.flush()

    @staticmethod
    async def log_event(session: AsyncSession, record: PostRecord, event: str, detail: Optional[str]) -> None:
        entry = PostLog(post_id=record.id, event=event, detail=detail, at=datetime.now(timezone.utc))
        session.add(entry)
        await session.flush()

    @staticmethod
    async def list_pending(
        session: AsyncSession,
        *,
        manual_only: Optional[bool] = None,
        limit: int = 50,
    ) -> list[PostRecord]:
        stmt = (
            select(PostRecord)
            .where(PostRecord.status == PostStatus.PENDING)
            .order_by(PostRecord.manual_review_required.desc(), PostRecord.queued_at.asc())
            .limit(limit)
        )
        if manual_only is True:
            stmt = stmt.where(PostRecord.manual_review_required.is_(True))
        elif manual_only is False:
            stmt = stmt.where(PostRecord.manual_review_required.is_(False))

        result = await session.execute(stmt)
        return list(result.scalars())

    @staticmethod
    async def get_recent_logs(session: AsyncSession, limit: int = 25) -> List[Dict[str, object]]:
        stmt = (
            select(PostLog, PostRecord.title)
            .join(PostRecord, PostRecord.id == PostLog.post_id)
            .order_by(PostLog.at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        logs: List[Dict[str, object]] = []
        for log_item, title in result.all():
            logs.append(
                {
                    "id": log_item.id,
                    "post_id": log_item.post_id,
                    "event": log_item.event,
                    "detail": log_item.detail,
                    "at": log_item.at,
                    "title": title,
                }
            )
        return logs

    @staticmethod
    async def get_summary_stats(session: AsyncSession) -> Dict[str, int]:
        """Aggregate counts for dashboard metrics."""

        counts = {
            "total": 0,
            "pending": 0,
            "posted": 0,
            "skipped": 0,
            "error": 0,
            "manual_required": 0,
            "telegram_posted": 0,
            "twitter_posted": 0,
        }

        status_stmt = (
            select(PostRecord.status, func.count().label("cnt"))
            .group_by(PostRecord.status)
        )
        status_rows = await session.execute(status_stmt)
        for status, cnt in status_rows.all():
            counts["total"] += cnt
            if status in counts:
                counts[status] = cnt

        manual_stmt = (
            select(func.count())
            .where(PostRecord.manual_review_required.is_(True))
            .where(PostRecord.status == PostStatus.PENDING)
        )
        counts["manual_required"] = (await session.execute(manual_stmt)).scalar_one()

        telegram_stmt = select(func.count()).where(
            PostRecord.channels.is_not(None),
        ).where(PostRecord.channels.like("%telegram%")).where(PostRecord.status == PostStatus.POSTED)
        counts["telegram_posted"] = (await session.execute(telegram_stmt)).scalar_one()

        twitter_stmt = select(func.count()).where(
            PostRecord.channels.is_not(None),
        ).where(PostRecord.channels.like("%x%")).where(PostRecord.status == PostStatus.POSTED)
        counts["twitter_posted"] = (await session.execute(twitter_stmt)).scalar_one()

        return counts
