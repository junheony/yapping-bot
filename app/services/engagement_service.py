"""Discovery and scoring for engagement opportunities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EngagementAction, MindshareSource
from app.services.engagement_repository import EngagementRepository
from app.services.mindshare_repository import MindshareRepository
from app.services.project_priority_loader import ProjectPriorityLoader
from app.services.strategy_loader import StrategyLoader
from app.services.usage_repository import UsageRepository
from app.services.twitter_client import twitter_client


class EngagementService:
    """Fetches candidate tweets and stores engagement tasks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EngagementRepository()
        self.mindshare_repo = MindshareRepository()
        self._mindshare_weights: Dict[str, float] = {}
        self.strategy_loader = StrategyLoader()
        self._strategy_weights = {
            handle: self.strategy_loader.get_weight(handle)
            for handle in self.strategy_loader.get_all_accounts()
        }
        self.project_loader = ProjectPriorityLoader() if settings.engagement_enable_project_bonus else None
        self._official_handles: Set[str] = set()
        self._priority_handles: Set[str] = {
            handle.lower().lstrip("@") for handle in settings.engagement_priority_handles
        }
        self.usage_repo = UsageRepository(session)
        self._max_mentions = settings.engagement_max_mentions
        self._handle_stats: Dict[str, dict] = {}

    async def discover(self) -> int:
        """Run discovery across configured queries."""

        if self.project_loader:
            self.project_loader.reload()

        mindshare_targets = await self.mindshare_repo.list_targets(
            self.session, limit=settings.engagement_mindshare_query_limit
        )
        self._handle_stats = await self.repo.handle_stats(self.session)
        self._mindshare_weights = {}
        self._official_handles = set()
        priority_handles: Set[str] = set()
        for index, target in enumerate(mindshare_targets):
            weight = 5.0
            if target.rank:
                weight += max(0, 50 - target.rank) * 0.1
            if target.score:
                try:
                    weight += float(target.score) * 0.05
                except (TypeError, ValueError):
                    pass
            stats_payload = target.stats or {}
            supabase_payload = stats_payload.get("supabase") or {}
            smart_followers = supabase_payload.get("smart_followers")
            real_followers = supabase_payload.get("real_followers")
            if smart_followers is not None:
                try:
                    smart_value = float(smart_followers)
                    weight += min(smart_value * settings.engagement_smart_follower_weight, 25.0)
                    if real_followers:
                        ratio = smart_value / max(float(real_followers) or 1.0, 1.0)
                        weight += ratio * 5.0
                except (TypeError, ValueError):
                    pass
            handle = (target.handle or "").lower()
            if not handle:
                continue
            if target.inner_circle:
                weight *= settings.engagement_inner_circle_multiplier
            self._mindshare_weights[handle] = weight
            stats = target.stats or {}
            is_official_source = target.source == MindshareSource.KAITO_SUPABASE
            is_supabase_entry = bool(stats.get("supabase"))
            if is_official_source or is_supabase_entry:
                self._official_handles.add(handle)
            if target.inner_circle and settings.engagement_include_inner_circle:
                priority_handles.add(handle)

        if self.project_loader:
            self.project_loader.update_mindshare_targets(mindshare_targets)
            self._official_handles.update(self.project_loader.official_handles())

        dynamic_queries = []
        topic_queries = settings.engagement_extra_queries or []
        dynamic_queries.extend(topic_queries)

        for handle in self.strategy_loader.get_all_accounts():
            query = f"from:{handle}"
            if query not in dynamic_queries:
                dynamic_queries.append(query)

        filtered_priority = {handle for handle in priority_handles if handle not in self._official_handles}
        config_priority = {handle.lower().lstrip("@") for handle in settings.engagement_priority_handles}
        project_handles: Set[str] = set()
        if self.project_loader:
            project_handles = {
                handle
                for project in self.project_loader.projects()
                for handle in project.community_handles
            }
        self._priority_handles = filtered_priority | config_priority | project_handles

        for handle in sorted(self._priority_handles):
            query = f"from:{handle}"
            if query not in dynamic_queries:
                dynamic_queries.append(query)

        deduped_queries = list(dict.fromkeys(dynamic_queries))
        max_queries = settings.engagement_max_dynamic_queries or len(deduped_queries)
        dynamic_queries = deduped_queries[:max_queries]

        total_added = 0

        for query in dynamic_queries:
            tweets, meta = await twitter_client.search_recent(
                query, max_results=settings.engagement_search_max_results
            )
            result_count = meta.get("result_count") if isinstance(meta, dict) else None
            try:
                fetched = int(result_count) if result_count is not None else len(tweets)
            except (TypeError, ValueError):
                fetched = len(tweets)
            if fetched:
                await self.usage_repo.increment(
                    "twitter",
                    "search_posts_pulled",
                    fetched,
                    reset_day=settings.twitter_usage_reset_day,
                    meta={"last_query": query},
                )
            await self.usage_repo.increment(
                "twitter",
                "search_requests",
                1,
                reset_day=settings.twitter_usage_reset_day,
                meta={"last_query": query},
            )

            for tweet in tweets:
                if await self.repo.get_by_tweet_id(self.session, tweet["tweet_id"]):
                    continue
                if tweet.get("is_retweet"):
                    continue
                if tweet.get("text", "").strip().lower().startswith("rt @"):
                    continue
                if self._exceeds_mention_limit(tweet.get("text", "")):
                    continue
                if self._contains_blacklist(tweet["text"]):
                    continue
                score = self._score(tweet.get("metrics") or {}, tweet.get("author_username"))
                project_match = None
                project_bonus = 0.0
                project_reason = ""
                if self.project_loader:
                    project_match, project_bonus, project_reason = self.project_loader.score_for_tweet(tweet)
                    if project_bonus:
                        score += project_bonus
                if score < settings.engagement_min_score:
                    continue
                action = self._pick_action(score)
                reason = f"query:{query}"
                author = (tweet.get("author_username") or "").lower()
                if author and author in self._official_handles:
                    continue
                bonus = self._mindshare_weights.get(author)
                if bonus:
                    reason += f"|mindshare_bonus:{bonus:.2f}"
                strategy_bonus = self._strategy_weights.get(author)
                if strategy_bonus:
                    reason += f"|strategy_bonus:{strategy_bonus:.2f}"
                if author and author in self._priority_handles:
                    reason += "|priority_handle"
                text = tweet.get("text", "")
                if any(keyword.lower() in text.lower() for keyword in settings.engagement_keyword_boosts):
                    score += settings.engagement_keyword_bonus
                    reason += "|boost_keyword"

                if project_match:
                    reason += f"|project:{project_match.key}"
                    if project_reason:
                        reason += f"|{project_reason}"
                        reason += f"|project_bonus:{project_bonus:.2f}"
                metrics_json = json.dumps(tweet.get("metrics") or {})
                task = await self.repo.create_task(
                    self.session,
                    tweet_id=tweet["tweet_id"],
                    tweet_url=tweet["url"],
                    text=tweet["text"],
                    action=action,
                    score=score,
                    metrics=metrics_json,
                    source_query=query,
                    author_id=tweet.get("author_id"),
                    author_username=tweet.get("author_username"),
                    reason=reason,
                )
                await self.repo.log_event(self.session, task, "discovered", f"Score={score:.2f}")
                total_added += 1

        logger.info(
            "Engagement discovery queued %s tasks (queries=%s, official_handles=%s, priority_handles=%s)",
            total_added,
            len(dynamic_queries),
            len(self._official_handles),
            len(self._priority_handles),
        )
        return total_added

    def _contains_blacklist(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword.lower() in lowered for keyword in settings.engagement_blacklist_keywords)

    def _exceeds_mention_limit(self, text: str) -> bool:
        if self._max_mentions <= 0:
            return False
        mention_count = text.count("@")
        return mention_count > self._max_mentions

    def _score(self, metrics: Dict[str, int], author_username: Optional[str]) -> float:
        likes = metrics.get("like_count", 0)
        retweets = metrics.get("retweet_count", 0)
        replies = metrics.get("reply_count", 0)
        weights = settings.engagement_score_weights
        if len(weights) < 3:
            weights = list(weights) + [0.1] * (3 - len(weights))
        base_score = likes * weights[0] + retweets * weights[1] + replies * weights[2]
        author = author_username.lower() if author_username else None
        bonus = 0.0
        if author:
            bonus += self._mindshare_weights.get(author, 0.0)
            bonus += self._strategy_weights.get(author, 0.0)
            handle_stats = self._handle_stats.get(author)
            if handle_stats:
                completed = handle_stats.get("completed", 0)
                last_completed_at = handle_stats.get("last_completed_at")
                if completed == 0:
                    bonus += settings.engagement_new_handle_bonus
                else:
                    if isinstance(last_completed_at, datetime):
                        last_seen = last_completed_at
                        if last_seen.tzinfo is None:
                            last_seen = last_seen.replace(tzinfo=timezone.utc)
                        else:
                            last_seen = last_seen.astimezone(timezone.utc)
                        elapsed_hours = (datetime.now(timezone.utc) - last_seen).total_seconds() / 3600.0
                        threshold = max(settings.engagement_handle_cooldown_hours, 1)
                        if (
                            completed >= settings.engagement_handle_cooldown_completed
                            and elapsed_hours < threshold
                        ):
                            decay = max(0.0, 1.0 - (elapsed_hours / threshold))
                            bonus -= settings.engagement_handle_cooldown_penalty * decay
            else:
                bonus += settings.engagement_new_handle_bonus
        return base_score + bonus

    def _pick_action(self, score: float) -> str:
        if not settings.engagement_reply_templates:
            return EngagementAction.LIKE
        if score >= settings.engagement_min_score * 2:
            return EngagementAction.REPLY
        return EngagementAction.LIKE
