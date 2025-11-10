#!/usr/bin/env python3
"""Collect recent posts from mindshare targets for analysis."""

import asyncio
from typing import List

from loguru import logger

from app.database import SessionMaker
from app.services.mindshare_repository import MindshareRepository
from app.services.research_repository import ResearchRepository
from app.services.strategy_loader import StrategyLoader
from app.services.twitter_client import twitter_client

MAX_POSTS_PER_HANDLE = 5
MAX_HANDLES = 2


async def collect() -> None:
    async with SessionMaker() as session:
        mindshare_repo = MindshareRepository()
        research_repo = ResearchRepository()
        strategy_loader = StrategyLoader()

        targets = await mindshare_repo.list_targets(session, limit=MAX_HANDLES)
        handles: List[str] = []
        seen = set()
        for target in targets:
            handle = (target.handle or "").lower()
            if handle and handle not in seen:
                seen.add(handle)
                handles.append(handle)
        # include strategy accounts for research coverage
        for handle in strategy_loader.get_all_accounts():
            if handle not in seen:
                handles.append(handle)
                seen.add(handle)
        if not handles:
            logger.warning("No mindshare targets found; please run mindshare_sync first.")
            return

        for handle in handles:
            query = f"from:{handle}"
            tweets, _meta = await twitter_client.search_recent(query, max_results=MAX_POSTS_PER_HANDLE)
            logger.info("Fetched %s tweets for handle %s", len(tweets), handle)
            for tweet in tweets:
                tweet_id = tweet.get("tweet_id")
                if not tweet_id:
                    continue
                if await research_repo.get_by_tweet_id(session, tweet_id):
                    continue
                await research_repo.add_post(
                    session,
                    tweet_id=tweet_id,
                    handle=handle,
                    text=tweet.get("text", ""),
                    metrics=tweet.get("metrics"),
                    source="mindshare",
                )
            await session.commit()


if __name__ == "__main__":
    asyncio.run(collect())
