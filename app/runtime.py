"""Centralized runtime services and background worker controls."""

from __future__ import annotations

from app.services.engagement_insights_worker import EngagementInsightsWorker
from app.services.engagement_worker import EngagementWorker
from app.services.queue_worker import QueueWorker
from app.services.rss_poller import RSSPoller

queue_worker = QueueWorker()
rss_poller = RSSPoller()
engagement_worker = EngagementWorker()
insights_worker = EngagementInsightsWorker()


def start_core_services(auto_engagement: bool = False) -> None:
    """Start background workers, optionally including engagement discovery."""

    queue_worker.start()
    rss_poller.start()
    insights_worker.start()
    if auto_engagement:
        engagement_worker.start()


async def stop_core_services() -> None:
    """Stop all background workers."""

    await queue_worker.stop()
    await rss_poller.stop()
    await insights_worker.stop()
    await engagement_worker.stop()


def start_engagement_worker() -> None:
    """Start the engagement worker if not already running."""

    engagement_worker.start()


async def stop_engagement_worker() -> None:
    """Stop the engagement worker if running."""

    await engagement_worker.stop()


def engagement_worker_running() -> bool:
    """Return True if the engagement worker task is active."""

    return engagement_worker.is_running()


def engagement_insights_running() -> bool:
    return insights_worker.is_running()
