"""Twitter API client for search and interactions."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, Dict, List, Optional

import httpx
from loguru import logger

from app.config import settings

SEARCH_ENDPOINT = "https://api.twitter.com/2/tweets/search/recent"
LIKE_ENDPOINT_TEMPLATE = "https://api.twitter.com/2/users/{user_id}/likes"
FOLLOW_ENDPOINT_TEMPLATE = "https://api.twitter.com/2/users/{user_id}/following"
REPLY_ENDPOINT = "https://api.twitter.com/2/tweets"


class TwitterClient:
    """Utility wrapper for X API search and engagement."""

    def __init__(self) -> None:
        self.bearer_token = settings.twitter_bearer_token
        self.user_id = settings.twitter_user_id
        self.api_key = settings.twitter_api_key
        self.api_secret = settings.twitter_api_secret
        self.access_token = settings.twitter_access_token
        self.access_secret = settings.twitter_access_secret
        self.enabled = bool(self.bearer_token and self.user_id)
        self.max_calls = settings.twitter_rate_max_calls
        self.window_seconds = settings.twitter_rate_window_seconds
        self.min_interval = settings.twitter_min_interval_seconds
        self._call_timestamps: Deque[float] = deque()
        self._rate_block_until: float = 0.0
        self._follow_cache: set[str] = set()

    def _auth_headers(self) -> Dict[str, str]:
        if not self.bearer_token:
            raise RuntimeError("Twitter bearer token missing.")
        return {"Authorization": f"Bearer {self.bearer_token}"}

    async def search_recent(self, query: str, max_results: int = 20) -> tuple[List[Dict[str, object]], Dict[str, object]]:
        if not self.enabled:
            logger.warning("Twitter search disabled; missing credentials.")
            return [], {}

        params = {
            "query": query,
            "max_results": str(max_results),
            "tweet.fields": "author_id,created_at,public_metrics,referenced_tweets,text",
            "expansions": "author_id",
            "user.fields": "username",
        }

        await self._throttle()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(SEARCH_ENDPOINT, headers=self._auth_headers(), params=params)

        if response.status_code == 429:
            reset_header = response.headers.get("x-rate-limit-reset")
            if reset_header and reset_header.isdigit():
                reset_time = int(reset_header)
                self._rate_block_until = max(self._rate_block_until, reset_time)
            else:
                self._rate_block_until = time.time() + self.window_seconds
            logger.warning("Twitter search hit rate limit; backing off until {}", self._rate_block_until)
            return [], {}

        if response.status_code >= 400:
            logger.error("Twitter search failed: {} - {}", response.status_code, response.text)
            return [], {}

        payload = response.json()
        meta = payload.get("meta", {}) or {}
        users = {user["id"]: user for user in payload.get("includes", {}).get("users", [])}
        results: List[Dict[str, object]] = []

        for tweet in payload.get("data", []):
            author = users.get(tweet.get("author_id")) or {}
            metrics = tweet.get("public_metrics") or {}
            references = tweet.get("referenced_tweets") or []
            is_retweet = any(ref.get("type") == "retweeted" for ref in references)
            results.append(
                {
                    "tweet_id": tweet["id"],
                    "text": tweet.get("text", ""),
                    "author_id": tweet.get("author_id"),
                    "author_username": author.get("username"),
                    "metrics": metrics,
                    "created_at": tweet.get("created_at"),
                    "url": f"https://twitter.com/{author.get('username', 'unknown')}/status/{tweet['id']}",
                    "is_retweet": is_retweet,
                }
            )
        return results, meta

    async def like_tweet(self, tweet_id: str) -> Dict[str, object]:
        if not self.enabled:
            logger.warning("Twitter like skipped; missing credentials.")
            return {"success": False, "reason": "disabled"}
        endpoint = LIKE_ENDPOINT_TEMPLATE.format(user_id=self.user_id)
        payload = {"tweet_id": tweet_id}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(endpoint, headers=self._auth_headers(), json=payload)
        if response.status_code >= 400:
            message = ""
            try:
                payload = response.json()
                message = payload.get("errors", [{}])[0].get("detail", "")
            except Exception:  # pragma: no cover - defensive
                message = response.text
            logger.error("Twitter like failed: {} - {}", response.status_code, message)
            return {"success": False, "reason": "error", "status": response.status_code, "message": message}
        return {"success": True, "reason": "liked", "status": response.status_code}

    async def reply_to_tweet(self, tweet_id: str, text: str) -> Dict[str, object]:
        if not self.enabled:
            logger.warning("Twitter reply skipped; missing credentials.")
            return {"success": False, "reason": "disabled"}
        payload = {"text": text, "reply": {"in_reply_to_tweet_id": tweet_id}}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(REPLY_ENDPOINT, headers=self._auth_headers(), json=payload)
        if response.status_code >= 400:
            message = ""
            try:
                payload = response.json()
                message = payload.get("errors", [{}])[0].get("detail", "")
            except Exception:  # pragma: no cover - defensive
                message = response.text
            logger.error("Twitter reply failed: {} - {}", response.status_code, message)
            return {"success": False, "reason": "error", "status": response.status_code, "message": message}
        data = {}
        try:
            data = response.json().get("data", {})
        except Exception:  # pragma: no cover - defensive
            data = {}
        reply_id = data.get("id")
        return {"success": True, "reason": "replied", "reply_id": reply_id, "status": response.status_code}

    async def get_tweet_metrics(self, tweet_id: str) -> Dict[str, Optional[int]]:
        if not self.enabled:
            return {}
        endpoint = f"https://api.twitter.com/2/tweets/{tweet_id}"
        params = {"tweet.fields": "public_metrics"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(endpoint, headers=self._auth_headers(), params=params)
        if response.status_code >= 400:
            logger.error("Twitter metrics failed: {} - {}", response.status_code, response.text)
            return {}
        try:
            data = response.json().get("data", {})
            metrics = data.get("public_metrics", {}) or {}
            return {
                "like_count": metrics.get("like_count"),
                "reply_count": metrics.get("reply_count"),
                "retweet_count": metrics.get("retweet_count"),
                "quote_count": metrics.get("quote_count"),
            }
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to parse tweet metrics: {}", exc)
            return {}

    async def user_follows_target(self, source_user_id: str, target_user_id: str) -> Optional[bool]:
        """Check if source user follows the target user (best-effort)."""

        if not self.enabled:
            return None
        endpoint = f"https://api.twitter.com/2/users/{source_user_id}/following"
        params: Dict[str, str] = {"max_results": "1000", "user.fields": "id"}
        next_token: Optional[str] = None
        try:
            while True:
                if next_token:
                    params["pagination_token"] = next_token
                else:
                    params.pop("pagination_token", None)
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(endpoint, headers=self._auth_headers(), params=params)
                if response.status_code == 401:
                    logger.warning("Follower lookup unauthorized for user %s", source_user_id)
                    return None
                if response.status_code >= 400:
                    logger.error("Follower lookup failed: {} - {}", response.status_code, response.text)
                    return None
                payload = response.json()
                users = payload.get("data") or []
                if any(user.get("id") == target_user_id for user in users):
                    return True
                meta = payload.get("meta") or {}
                next_token = meta.get("next_token")
                if not next_token:
                    return False
        except Exception as exc:  # pragma: no cover
            logger.error("Follower lookup error: {}", exc)
            return None

    async def follow_user(self, target_user_id: Optional[str]) -> Dict[str, object]:
        """Attempt to follow the given user id, returning metadata about the call."""

        if not self.enabled:
            logger.warning("Twitter follow skipped; missing credentials.")
            return {"success": False, "reason": "disabled"}
        if not self.user_id:
            logger.warning("Twitter follow skipped; missing bot user id.")
            return {"success": False, "reason": "missing_user"}
        if not target_user_id:
            return {"success": False, "reason": "missing_target"}
        if target_user_id == self.user_id:
            return {"success": False, "reason": "self_follow"}
        if target_user_id in self._follow_cache:
            return {"success": True, "reason": "cached"}

        endpoint = FOLLOW_ENDPOINT_TEMPLATE.format(user_id=self.user_id)
        payload = {"target_user_id": target_user_id}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(endpoint, headers=self._auth_headers(), json=payload)

        if response.status_code >= 400:
            message = ""
            try:
                payload = response.json()
                message = payload.get("errors", [{}])[0].get("detail", "")
            except Exception:  # pragma: no cover - defensive
                message = response.text
            logger.error("Twitter follow failed: {} - {}", response.status_code, message)
            return {"success": False, "reason": "error", "status": response.status_code, "message": message}

        data = {}
        try:
            data = response.json().get("data", {})
        except Exception:  # pragma: no cover - defensive
            data = {}

        following = bool(data.get("following"))
        pending = bool(data.get("pending_follow"))
        if following or pending or response.status_code == 200:
            self._follow_cache.add(target_user_id)
            return {
                "success": True,
                "reason": "pending" if pending and not following else "followed",
                "pending": pending,
                "already": following and not pending,
            }

        return {"success": False, "reason": "unknown"}

    async def _throttle(self) -> None:
        now = time.time()
        if self._rate_block_until and now < self._rate_block_until:
            await asyncio.sleep(self._rate_block_until - now)
            now = time.time()

        if self._call_timestamps:
            elapsed = now - self._call_timestamps[-1]
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
                now = time.time()

        while self._call_timestamps and now - self._call_timestamps[0] > self.window_seconds:
            self._call_timestamps.popleft()

        if len(self._call_timestamps) >= self.max_calls:
            sleep_time = self.window_seconds - (now - self._call_timestamps[0]) + 1
            await asyncio.sleep(max(sleep_time, self.min_interval))
            await self._throttle()
            return

        self._call_timestamps.append(time.time())


twitter_client = TwitterClient()
