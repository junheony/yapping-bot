"""Persistence helpers for engagement outcomes."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EngagementOutcome


class EngagementOutcomeRepository:
    """CRUD operations for engagement outcomes."""

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        task_id: int,
        tweet_id: str,
        author_id: Optional[str],
        author_username: Optional[str],
        reply_tweet_id: Optional[str],
        like_count: Optional[int],
        reply_count: Optional[int],
        author_following: Optional[bool],
        replied: Optional[bool],
        notes: Optional[str],
        checked_at,
    ) -> EngagementOutcome:
        stmt = select(EngagementOutcome).where(EngagementOutcome.task_id == task_id)
        result = await session.execute(stmt)
        outcome = result.scalars().first()
        if outcome is None:
            outcome = EngagementOutcome(
                task_id=task_id,
                tweet_id=tweet_id,
                author_id=author_id,
                author_username=author_username,
                reply_tweet_id=reply_tweet_id,
                like_count=like_count,
                reply_count=reply_count,
                author_following=author_following,
                replied=replied,
                checked_at=checked_at,
                notes=notes,
            )
            session.add(outcome)
        else:
            outcome.tweet_id = tweet_id
            outcome.author_id = author_id
            outcome.author_username = author_username
            outcome.reply_tweet_id = reply_tweet_id
            outcome.like_count = like_count
            outcome.reply_count = reply_count
            outcome.author_following = author_following
            outcome.replied = replied
            outcome.checked_at = checked_at
            outcome.notes = notes
        await session.flush()
        return outcome

    @staticmethod
    async def recent(session: AsyncSession, limit: int = 25) -> list[EngagementOutcome]:
        stmt = select(EngagementOutcome).order_by(EngagementOutcome.checked_at.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars())
