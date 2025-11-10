"""Central processing pipeline for incoming yaps."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import List

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import PostRecord, PostStatus
from app.schemas import GeneratedCopy, PostingResult, YapPayload, YapResponse
from app.services.google_logger import GoogleSheetsLogger
from app.services.openai_client import OpenAICopyClient
from app.services.posters.telegram import TelegramPoster
from app.services.posters.twitter import TwitterPoster
from app.services.repository import PostRepository
from app.services.text_filters import apply_filters
from app.utils.hash import url_hash


google_logger = GoogleSheetsLogger()


class YapProcessor:
    """Process a payload through filters, generation, and posting."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PostRepository()
        self.openai = OpenAICopyClient()
        self.telegram = TelegramPoster()
        self.twitter = TwitterPoster()
        self.google_logger = google_logger

    async def handle(self, payload: YapPayload) -> YapResponse:
        """Process direct webhook payload."""

        hash_value = url_hash(str(payload.url))
        existing = await self.repo.get_by_hash(self.session, hash_value)
        if existing:
            logger.info("Duplicate payload detected for {}", payload.url)
            await self.repo.log_event(self.session, existing, "duplicate", "Duplicate payload received.")
            return YapResponse(
                hash=hash_value,
                status=existing.status,
                posted_at=existing.posted_at,
                message="Duplicate payload.",
            )

        allowed, reason, manual = apply_filters(payload)

        record = await self.repo.create(
            self.session,
            hash_value=hash_value,
            title=payload.title,
            url=str(payload.url),
            source=payload.source,
            source_type=payload.source_type or "webhook",
            lang=payload.lang,
            tags=",".join(payload.tags) if payload.tags else None,
            prompt_version=settings.prompt_version,
            manual_review_required=manual,
            approved=not manual,
            queued_at=datetime.now(timezone.utc),
            priority=0,
        )
        await self.repo.log_event(self.session, record, "ingested", reason)

        if not allowed:
            await self.repo.mark_status(
                self.session,
                record,
                status=PostStatus.SKIPPED,
                error_msg=reason,
            )
            await self.repo.log_event(self.session, record, "skipped", reason)
            return YapResponse(hash=hash_value, status=PostStatus.SKIPPED, message=reason)
        if manual:
            await self.telegram.notify_owner(f"📝 Manual review required for: {payload.title}")
            await self.repo.log_event(self.session, record, "manual_required", "Awaiting manual approval.")
            await self.google_logger.log_queue_enqueue(record, "manual_review_required")
            return YapResponse(
                hash=hash_value,
                status=PostStatus.PENDING,
                message="Pending manual approval.",
            )

        return await self._process_record(record, payload)

    async def process_record(self, record: PostRecord) -> YapResponse:
        """Process an existing queued record."""

        payload = YapPayload(
            title=record.title,
            url=record.url,
            source=record.source,
            source_type=record.source_type or "queue",
            lang=record.lang,
            tags=record.tags.split(",") if record.tags else [],
        )
        return await self._process_record(record, payload)

    async def _apply_random_delay(self) -> None:
        """Sleep for a random interval to avoid rate patterns."""

        low = settings.post_random_delay_min_seconds
        high = settings.post_random_delay_max_seconds

        if high <= 0 or high < low:
            return

        delay_seconds = random.randint(low, high)
        logger.info("Sleeping for {} seconds before posting.", delay_seconds)
        await asyncio.sleep(delay_seconds)

    async def _dispatch_posts(self, generated: GeneratedCopy, url: str) -> List[PostingResult]:
        """Send generated copy to configured channels."""

        tasks = [
            self.telegram.post(generated, url),
            self.twitter.post(generated, url),
        ]
        return await asyncio.gather(*tasks)

    @staticmethod
    def _resolve_status(results: List[PostingResult]) -> str:
        """Derive overall status from channel results."""

        if any(result.status == "posted" for result in results):
            return PostStatus.POSTED
        if any(result.status == "error" for result in results):
            return PostStatus.ERROR
        return PostStatus.SKIPPED

    async def _process_record(self, record: PostRecord, payload: YapPayload) -> YapResponse:
        """Generate copy if missing and dispatch posts."""

        try:
            if not record.text_ko or not record.text_en:
                generated = await self.openai.generate_copy(payload)
                await self.repo.update_with_generation(
                    self.session,
                    record,
                    text_ko=generated.text_ko,
                    text_en=generated.text_en,
                    hashtags=generated.hashtags,
                    token_usage=generated.token_usage,
                )
                await self.repo.log_event(self.session, record, "generated", "Copy generated via OpenAI.")
            else:
                generated = GeneratedCopy(
                    text_ko=record.text_ko,
                    text_en=record.text_en,
                    hashtags=record.hashtags or "",
                    prompt_version=record.prompt_version or settings.prompt_version,
                    token_usage=record.token_usage,
                )
        except Exception as exc:  # pragma: no cover - external API failure path
            if not settings.openai_api_key:
                generated = GeneratedCopy(
                    text_ko=f"[stub] {payload.title}",
                    text_en=f"[stub] {payload.title}",
                    hashtags="#crypto #update",
                    prompt_version="STUB_NO_OPENAI",
                    token_usage=None,
                )
                await self.repo.update_with_generation(
                    self.session,
                    record,
                    text_ko=generated.text_ko,
                    text_en=generated.text_en,
                    hashtags=generated.hashtags,
                    token_usage=generated.token_usage,
                )
                await self.repo.log_event(self.session, record, "generated_stub", "OpenAI key missing; stub copy used.")
            else:
                logger.exception("OpenAI generation failed")
                await self.repo.mark_status(
                    self.session,
                    record,
                    status=PostStatus.ERROR,
                    error_msg=str(exc),
                    increment_attempt=True,
                    next_attempt_at=datetime.now(timezone.utc),
                )
                await self.repo.log_event(self.session, record, "error", str(exc))
                raise

        await self._apply_random_delay()
        await self.repo.log_event(self.session, record, "dispatching", "Posting to configured channels.")

        results = await self._dispatch_posts(generated, str(payload.url))
        final_status = self._resolve_status(results)
        channels = ",".join(result.channel for result in results if result.status != "skipped") or None
        posted_at = datetime.now(timezone.utc) if final_status == PostStatus.POSTED else None

        await self.repo.mark_status(
            self.session,
            record,
            status=final_status,
            channels=channels,
            posted_at=posted_at,
            error_msg=None if final_status == PostStatus.POSTED else record.error_msg,
            clear_next_attempt=True,
        )
        await self.repo.log_event(self.session, record, final_status, "Completed post dispatch.")
        await self.google_logger.log_post(record, final_status)

        if final_status == PostStatus.POSTED:
            await self.telegram.notify_owner(f"✅ Posted: {record.title}")
        elif final_status == PostStatus.ERROR:
            await self.telegram.notify_owner(f"❌ Post failed: {record.title} ({record.error_msg})")

        return YapResponse(hash=record.hash, status=final_status, posted_at=posted_at, posts=results, message="ok")
