"""Background worker that captures post-engagement outcomes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from app.config import settings
from app.database import SessionMaker
from app.services.engagement_outcome_repository import EngagementOutcomeRepository
from app.services.engagement_repository import EngagementRepository
from app.services.twitter_client import twitter_client


class EngagementInsightsWorker:
    """Fetch metrics after engagements to gauge effectiveness."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="engagement-insights-worker")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def _run(self) -> None:
        interval = max(settings.engagement_poll_interval_seconds, 90)
        while not self._stop_event.is_set():
            try:
                await self._process_cycle()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Engagement insights error: {}", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    async def _process_cycle(self) -> None:
        async with SessionMaker() as session:
            repo = EngagementRepository()
            tasks = await repo.list_completed_without_outcome(session, limit=10)
            if not tasks:
                await session.rollback()
                return

            outcome_repo = EngagementOutcomeRepository()
            for task in tasks:
                outcome = await self._capture_outcome(task)
                await outcome_repo.upsert(
                    session,
                    task_id=task.id,
                    tweet_id=task.tweet_id,
                    author_id=task.author_id,
                    author_username=task.author_username,
                    reply_tweet_id=getattr(task, "response_tweet_id", None),
                    like_count=outcome.get("like_count"),
                    reply_count=outcome.get("reply_count"),
                    author_following=outcome.get("author_following"),
                    replied=outcome.get("replied"),
                    notes=outcome.get("notes"),
                    checked_at=datetime.now(timezone.utc),
                )
            await session.commit()

    async def _capture_outcome(self, task) -> dict:
        tweet_metrics = await twitter_client.get_tweet_metrics(task.tweet_id)
        like_count = tweet_metrics.get("like_count") if tweet_metrics else None
        reply_count = tweet_metrics.get("reply_count") if tweet_metrics else None

        replied = False
        notes: list[str] = []
        if task.author_username and task.tweet_id:
            query = f"conversation_id:{task.tweet_id} from:{task.author_username}"
            replies, _ = await twitter_client.search_recent(query, max_results=10)
            if replies:
                replied = True
            else:
                notes.append("no_author_reply")

        author_following = None
        if task.author_id:
            author_following = await twitter_client.user_follows_target(task.author_id, settings.twitter_user_id)
            if author_following is None:
                notes.append("follow_check_failed")
        else:
            notes.append("no_author_id")

        return {
            "like_count": like_count,
            "reply_count": reply_count,
            "author_following": author_following,
            "replied": replied,
            "notes": "|".join(notes) if notes else None,
        }
