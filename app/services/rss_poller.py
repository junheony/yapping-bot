"""Background RSS poller to enqueue new items."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import feedparser
from loguru import logger

from app.config import settings
from app.database import SessionMaker
from app.models import PostStatus
from app.schemas import YapPayload
from app.services.google_logger import GoogleSheetsLogger
from app.services.repository import PostRepository
from app.services.text_filters import apply_filters
from app.utils.hash import url_hash


google_logger = GoogleSheetsLogger()


class RSSPoller:
    """Periodically poll configured RSS feeds and enqueue new posts."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._tz = ZoneInfo(settings.local_timezone)

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        if not settings.rss_feeds:
            logger.info("RSS poller disabled: no feeds configured.")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="rss-poller")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task

    async def _run(self) -> None:
        logger.info("RSS poller started.")
        interval = max(settings.rss_poll_interval_minutes, 1) * 60
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("RSS poll failed: {}", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
        logger.info("RSS poller stopped.")

    async def _poll_once(self) -> None:
        now_local = datetime.now(self._tz)
        if settings.rss_active_hours_local and now_local.hour not in settings.rss_active_hours_local:
            logger.debug("RSS poll skipped, outside active hours.")
            return

        async with SessionMaker() as session:
            repo = PostRepository()

            for feed_url in settings.rss_feeds:
                parsed = feedparser.parse(feed_url)
                if getattr(parsed, "bozo", False):
                    logger.warning("RSS feed parse error for %s: %s", feed_url, getattr(parsed, "bozo_exception", ""))
                    continue

                for entry in parsed.entries:
                    link = entry.get("link")
                    title = entry.get("title")
                    if not link or not title:
                        continue

                    payload = YapPayload(
                        title=title,
                        url=link,
                        source=entry.get("source", {}).get("title") if entry.get("source") else parsed.feed.get("title"),
                        source_type="rss",
                        tags=self._extract_tags(entry),
                    )

                    hash_value = url_hash(link)
                    existing = await repo.get_by_hash(session, hash_value)
                    if existing:
                        continue

                    allowed, reason, manual = apply_filters(payload)

                    record = await repo.create(
                        session,
                        hash_value=hash_value,
                        title=payload.title,
                        url=str(payload.url),
                        source=payload.source,
                        source_type=payload.source_type,
                        lang=payload.lang,
                        tags=",".join(payload.tags) if payload.tags else None,
                        prompt_version=settings.prompt_version,
                        manual_review_required=manual,
                        approved=not manual,
                        queued_at=datetime.now(timezone.utc),
                        priority=0,
                    )

                    if not allowed:
                        await repo.mark_status(session, record, status=PostStatus.SKIPPED, error_msg=reason)
                        await repo.log_event(session, record, "skipped", reason)
                        await google_logger.log_queue_enqueue(record, f"filtered:{reason}")
                        continue

                    await repo.log_event(session, record, "queued", f"Enqueued from RSS: {feed_url}")
                    await google_logger.log_queue_enqueue(record, f"rss:{feed_url}")

                    if manual:
                        await repo.log_event(session, record, "manual_required", "Manual review from RSS.")
                        continue

                    # Leave for queue worker to process asynchronously.
                    await session.flush()

            await session.commit()

    @staticmethod
    def _extract_tags(entry) -> list[str]:
        tags = []
        for tag in entry.get("tags", []):
            term = tag.get("term")
            if term:
                tags.append(term)
        return tags
