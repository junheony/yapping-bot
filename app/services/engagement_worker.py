"""Background worker to execute engagement actions."""

from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, timezone
from typing import Optional

from loguru import logger

from app.config import settings
from app.database import SessionMaker
from app.models import EngagementAction, EngagementStatus
from app.services.engagement_repository import EngagementRepository
from app.services.engagement_service import EngagementService
from app.services.twitter_client import twitter_client


class EngagementWorker:
    """Handles discovery and execution of likes/replies."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._current_day = date.today()
        self._likes_today = 0
        self._replies_today = 0

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="engagement-worker")

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        logger.info("Engagement worker started.")
        interval = max(settings.engagement_poll_interval_seconds, 60)
        while not self._stop_event.is_set():
            try:
                await self._reset_if_new_day()
                await self._discover_cycle()
                await self._process_cycle()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Engagement worker error: {}", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
        logger.info("Engagement worker stopped.")

    async def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._current_day:
            self._current_day = today
            self._likes_today = 0
            self._replies_today = 0

    async def _discover_cycle(self) -> None:
        if not settings.twitter_enabled:
            return
        async with SessionMaker() as session:
            service = EngagementService(session)
            added = await service.discover()
            if added:
                await session.commit()
            else:
                await session.rollback()

    async def _process_cycle(self) -> None:
        if not twitter_client.enabled:
            return

        async with SessionMaker() as session:
            repo = EngagementRepository()
            tasks = await repo.pick_pending_tasks(session, limit=10)
            if not tasks:
                return

            for task in tasks:
                if task.action == EngagementAction.LIKE and self._likes_today >= settings.engagement_daily_like_limit:
                    await repo.mark_status(session, task, status=EngagementStatus.SKIPPED)
                    await repo.log_event(session, task, "skipped", "Daily like limit reached.")
                    continue
                if task.action == EngagementAction.REPLY and self._replies_today >= settings.engagement_daily_reply_limit:
                    await repo.mark_status(session, task, status=EngagementStatus.SKIPPED)
                    await repo.log_event(session, task, "skipped", "Daily reply limit reached.")
                    continue

                await repo.mark_status(session, task, status=EngagementStatus.PROCESSING, increment_attempt=True)
                await repo.log_event(session, task, "processing", "Attempting action.")

                result = await self._execute_task(task)
                await self._attempt_follow(session, repo, task)
                if result.get("success"):
                    await repo.mark_status(
                        session,
                        task,
                        status=EngagementStatus.COMPLETED,
                        processed_at=datetime.now(timezone.utc),
                    )
                    detail = self._format_action_detail(task, result)
                    await repo.log_event(session, task, "completed", detail)
                    if task.action == EngagementAction.REPLY and result.get("reply_id"):
                        task.response_tweet_id = result.get("reply_id")
                    if task.action == EngagementAction.LIKE:
                        self._likes_today += 1
                    else:
                        self._replies_today += 1
                else:
                    await repo.mark_status(session, task, status=EngagementStatus.ERROR)
                    detail = self._format_action_detail(task, result)
                    await repo.log_event(session, task, "error", detail)
            await session.commit()

    async def _execute_task(self, task) -> dict:
        if task.action == EngagementAction.LIKE:
            return await twitter_client.like_tweet(task.tweet_id)

        reply_text = self._select_reply_template(task)
        return await twitter_client.reply_to_tweet(task.tweet_id, reply_text)

    async def _attempt_follow(self, session, repo: EngagementRepository, task) -> None:
        """Follow the tweet author if possible, logging the outcome."""

        if not twitter_client.enabled:
            return
        author_id = getattr(task, "author_id", None)
        if not author_id:
            return
        if author_id == settings.twitter_user_id:
            return

        result = await twitter_client.follow_user(author_id)
        username = getattr(task, "author_username", None) or author_id

        if not result.get("success"):
            reason = result.get("message") or result.get("reason") or "unknown"
            await repo.log_event(session, task, "follow_error", f"{username}: {reason}")
            return

        outcome = result.get("reason")
        if outcome == "cached":
            await repo.log_event(session, task, "follow_skipped", f"{username}: already followed (cached).")
        elif outcome == "pending":
            await repo.log_event(session, task, "follow_pending", f"{username}: follow request pending.")
        elif result.get("already"):
            await repo.log_event(session, task, "follow_skipped", f"{username}: already following.")
        else:
            await repo.log_event(session, task, "followed", f"{username}: follow established.")

    def _format_action_detail(self, task, result: dict) -> str:
        """Build a short summary string for action outcome."""

        base = f"{task.action} {'succeeded' if result.get('success') else 'failed'}."
        reason = result.get("message") or result.get("reason")
        if reason:
            base += f" {reason}"
        if task.action == EngagementAction.REPLY and result.get("reply_id"):
            base += f" reply_id={result['reply_id']}"
        return base

    def _select_reply_template(self, task) -> str:
        if not settings.engagement_reply_templates:
            return "Appreciate the update."
        chosen = random.choice(settings.engagement_reply_templates)
        if "{user}" in chosen and task.author_username:
            return chosen.replace("{user}", f"@{task.author_username}")
        return chosen
