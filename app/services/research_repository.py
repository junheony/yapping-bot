"""Repository for storing research posts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResearchPost


class ResearchRepository:
    async def get_by_tweet_id(self, session: AsyncSession, tweet_id: str) -> Optional[ResearchPost]:
        result = await session.execute(select(ResearchPost).where(ResearchPost.tweet_id == tweet_id))
        return result.scalars().first()

    async def add_post(
        self,
        session: AsyncSession,
        *,
        tweet_id: str,
        handle: str,
        text: str,
        metrics: Optional[dict],
        source: str,
    ) -> ResearchPost:
        post = ResearchPost(
            tweet_id=tweet_id,
            handle=handle,
            text=text,
            metrics=metrics,
            source=source,
            captured_at=datetime.now(timezone.utc),
        )
        session.add(post)
        await session.flush()
        return post
