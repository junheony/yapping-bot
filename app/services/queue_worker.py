"""Background queue worker for draining pending posts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from app.config import settings
from app.database import SessionMaker
from app.models import PostStatus
from app.services.pipeline import YapProcessor
from app.services.repository import PostRepository


class QueueWorker:
    """Periodically drains pending posts and processes them."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="queue-worker")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stopping.set()
        await self._task

    async def _run(self) -> None:
        logger.info("Queue worker started.")
        while not self._stopping.is_set():
            try:
                await self._drain_once()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Queue worker error: {}", exc)

            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=settings.queue_poll_interval_seconds)
            except asyncio.TimeoutError:
                continue
        logger.info("Queue worker stopped.")

    async def _drain_once(self) -> None:
        now = datetime.now(timezone.utc)
        async with SessionMaker() as session:
            repo = PostRepository()
            records = await repo.get_pending_queue(
                session,
                limit=settings.queue_batch_size,
                now=now,
                require_manual=False,
            )

            if not records:
                return

            processor = YapProcessor(session)

            for record in records:
                await repo.mark_status(
                    session,
                    record,
                    status=PostStatus.PENDING,
                    increment_attempt=True,
                    next_attempt_at=now,
                )
                await repo.log_event(session, record, "queue_attempt", "Queue worker picked up item.")
                try:
                    await processor.process_record(record)
                    await session.commit()
                except Exception as exc:  # pragma: no cover - external failure
                    logger.exception("Failed to process queued record %s: %s", record.id, exc)
                    await repo.mark_status(
                        session,
                        record,
                        status=PostStatus.ERROR,
                        error_msg=str(exc),
                        next_attempt_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    )
                    await repo.log_event(session, record, "error", str(exc))
                    await session.commit()
