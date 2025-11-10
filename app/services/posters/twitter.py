"""Twitter (X) posting service."""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from requests_oauthlib import OAuth1Session

from app.config import settings
from app.schemas import GeneratedCopy, PostingResult


class TwitterPoster:
    """Handles posting generated text to X API."""

    def __init__(self) -> None:
        self.enabled = settings.twitter_enabled
        self.api_key = settings.twitter_api_key
        self.api_secret = settings.twitter_api_secret
        self.access_token = settings.twitter_access_token
        self.access_secret = settings.twitter_access_secret

    async def post(self, copy: GeneratedCopy, url: str) -> PostingResult:
        """Post to X or skip if disabled."""

        if not self.enabled:
            return PostingResult(channel="x", status="skipped", detail="Twitter posting disabled.")

        missing = [
            name
            for name, value in {
                "TWITTER_API_KEY": self.api_key,
                "TWITTER_API_SECRET": self.api_secret,
                "TWITTER_ACCESS_TOKEN": self.access_token,
                "TWITTER_ACCESS_SECRET": self.access_secret,
            }.items()
            if not value
        ]
        if missing:
            logger.warning("Twitter credentials missing: {}", ", ".join(missing))
            return PostingResult(channel="x", status="skipped", detail="Twitter credentials incomplete.")

        tweet_text = self._build_tweet(copy, url)

        try:
            response = await asyncio.to_thread(self._post_tweet, tweet_text)
        except Exception as exc:  # pragma: no cover - network failure
            logger.exception("Twitter post failed: {}", exc)
            return PostingResult(channel="x", status="error", detail=str(exc))

        if response.status_code not in (200, 201):
            logger.error("Twitter API error %s: %s", response.status_code, response.text)
            return PostingResult(channel="x", status="error", detail=response.text)

        return PostingResult(channel="x", status="posted", detail="ok")

    def _post_tweet(self, text: str):
        """Send POST request using OAuth1 session (blocking)."""

        session = OAuth1Session(
            self.api_key,
            client_secret=self.api_secret,
            resource_owner_key=self.access_token,
            resource_owner_secret=self.access_secret,
        )
        return session.post("https://api.twitter.com/2/tweets", json={"text": text})

    @staticmethod
    def _build_tweet(copy: GeneratedCopy, url: str) -> str:
        """Construct tweet text within 280 characters."""

        parts = [copy.text_ko, copy.text_en, copy.hashtags, url]
        tweet = "\n".join(part for part in parts if part)
        if len(tweet) <= 280:
            return tweet

        parts = [copy.text_ko, copy.hashtags, url]
        tweet = "\n".join(part for part in parts if part)
        if len(tweet) <= 280:
            return tweet

        # Truncate Korean text as final fallback.
        suffix_parts = [part for part in parts[1:] if part]
        suffix = "\n".join(suffix_parts)
        available = 280 - len(suffix) - (1 if suffix else 0)
        truncated = copy.text_ko[: max(available - 1, 0)] + ("…" if available > 0 else "")
        return "\n".join([truncated, suffix]) if suffix else truncated
