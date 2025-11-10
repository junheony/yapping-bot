"""Telegram posting service."""

from typing import Optional

import httpx
from loguru import logger

from app.config import settings
from app.schemas import GeneratedCopy, PostingResult


class TelegramPoster:
    """Send generated messages to Telegram channels."""

    def __init__(self) -> None:
        self.token = settings.telegram_bot_token
        self.channel_id = settings.telegram_channel_id
        self.owner_id = settings.telegram_owner_id

    def _is_configured(self) -> bool:
        return bool(self.token and self.channel_id)

    def _build_message(self, copy: GeneratedCopy, url: str) -> str:
        parts = [
            f"[KO] {copy.text_ko}",
            f"[EN] {copy.text_en}",
            f"[TAG] {copy.hashtags}",
            url,
        ]
        return "\n".join(parts)

    async def post(self, copy: GeneratedCopy, url: str) -> PostingResult:
        """Send message to the configured channel."""

        if not self._is_configured():
            return PostingResult(channel="telegram", status="skipped", detail="Telegram not configured.")

        message = self._build_message(copy, url)
        api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.channel_id, "text": message, "disable_web_page_preview": False}

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(api_url, json=payload)

        if response.status_code >= 400:
            logger.error("Telegram post failed: {} - {}", response.status_code, response.text)
            return PostingResult(channel="telegram", status="error", detail=response.text)

        return PostingResult(channel="telegram", status="posted", detail="ok")

    async def notify_owner(self, message: str) -> None:
        """Send a DM to the owner if configured."""

        if not (self.token and self.owner_id):
            return

        api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.owner_id, "text": message, "disable_web_page_preview": True}

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(api_url, json=payload)

        if response.status_code >= 400:
            logger.warning("Owner notification failed: {} - {}", response.status_code, response.text)

