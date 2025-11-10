"""Repository helpers for engagement workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import case, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine
from app.models import EngagementAction, EngagementLog, EngagementOutcome, EngagementStatus, EngagementTask


class EngagementRepository:
    """CRUD helpers for engagement tasks."""

    @staticmethod
    async def ensure_schema() -> None:
        async with engine.begin() as conn:

            def ensure(sync_conn) -> None:
                inspector = inspect(sync_conn)
                columns = {col["name"] for col in inspector.get_columns("engagement_tasks")}
                if "response_tweet_id" not in columns:
                    sync_conn.execute(
                        text("ALTER TABLE engagement_tasks ADD COLUMN response_tweet_id VARCHAR(64)")
                    )
                if "engagement_outcomes" not in inspector.get_table_names():
                    EngagementOutcome.__table__.create(sync_conn)

            await conn.run_sync(ensure)

    @staticmethod
    async def get_by_tweet_id(session: AsyncSession, tweet_id: str) -> Optional[EngagementTask]:
        stmt = select(EngagementTask).where(EngagementTask.tweet_id == tweet_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def create_task(
        session: AsyncSession,
        *,
        tweet_id: str,
        tweet_url: str,
        text: str,
        action: str,
        score: float,
        metrics: Optional[str],
        source_query: Optional[str],
        author_id: Optional[str],
        author_username: Optional[str],
        reason: Optional[str],
    ) -> EngagementTask:
        task = EngagementTask(
            tweet_id=tweet_id,
            tweet_url=tweet_url,
            text=text,
            action=action,
            score=score,
            metrics=metrics,
            source_query=source_query,
            author_id=author_id,
            author_username=author_username,
            reason=reason,
        )
        session.add(task)
        await session.flush()
        return task

    @staticmethod
    async def mark_status(
        session: AsyncSession,
        task: EngagementTask,
        *,
        status: str,
        processed_at: Optional[datetime] = None,
        increment_attempt: bool = False,
    ) -> None:
        task.status = status
        task.processed_at = processed_at
        if increment_attempt:
            task.attempt_count += 1
        await session.flush()

    @staticmethod
    async def pick_pending_tasks(session: AsyncSession, limit: int = 20) -> List[EngagementTask]:
        stmt = (
            select(EngagementTask)
            .where(EngagementTask.status == EngagementStatus.PENDING)
            .order_by(EngagementTask.score.desc().nullslast(), EngagementTask.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars())

    @staticmethod
    async def log_event(session: AsyncSession, task: EngagementTask, event: str, detail: Optional[str]) -> None:
        entry = EngagementLog(task_id=task.id, event=event, detail=detail)
        session.add(entry)
        await session.flush()

    @staticmethod
    async def list_completed_without_outcome(session: AsyncSession, limit: int = 20) -> List[EngagementTask]:
        stmt = (
            select(EngagementTask)
            .outerjoin(EngagementOutcome, EngagementOutcome.task_id == EngagementTask.id)
            .where(EngagementTask.status == EngagementStatus.COMPLETED)
            .where(EngagementOutcome.task_id.is_(None))
            .order_by(EngagementTask.processed_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars())

    @staticmethod
    async def get_summary(session: AsyncSession) -> Dict[str, int]:
        stmt = select(EngagementTask.status, EngagementTask.action)
        result = await session.execute(stmt)
        counts = {
            "total": 0,  # completed interactions only
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "skipped": 0,
            "error": 0,
            "like": 0,
            "reply": 0,
            "queued": 0,  # all tasks regardless of status
        }
        status_key = {
            EngagementStatus.PENDING: "pending",
            EngagementStatus.PROCESSING: "processing",
            EngagementStatus.COMPLETED: "completed",
            EngagementStatus.SKIPPED: "skipped",
            EngagementStatus.ERROR: "error",
        }

        for status, action in result.all():
            counts["queued"] += 1
            key = status_key.get(status)
            if key:
                counts[key] += 1
            if status == EngagementStatus.COMPLETED:
                counts["total"] += 1
                if action in (EngagementAction.LIKE, EngagementAction.REPLY):
                    counts[action] += 1

        # ensure completed mirrors total for display compatibility
        counts["completed"] = counts["total"]
        return counts

    @staticmethod
    async def list_recent_tasks(session: AsyncSession, limit: int = 25) -> List[EngagementTask]:
        stmt = (
            select(EngagementTask)
            .order_by(EngagementTask.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars())

    @staticmethod
    async def recent_logs(session: AsyncSession, limit: int = 40) -> List[Dict[str, str]]:
        stmt = (
            select(EngagementLog, EngagementTask)
            .join(EngagementTask, EngagementTask.id == EngagementLog.task_id)
            .order_by(EngagementLog.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        logs: List[Dict[str, str]] = []
        for log_entry, task in result.all():
            logs.append(
                {
                    "id": log_entry.id,
                    "event": log_entry.event,
                    "detail": log_entry.detail,
                    "tweet_id": task.tweet_id,
                    "tweet_url": task.tweet_url,
                    "action": task.action,
                    "reason": task.reason,
                    "author": task.author_username,
                    "at": log_entry.created_at,
                }
            )
        return logs

    @staticmethod
    async def handle_stats(session: AsyncSession) -> Dict[str, dict]:
        """Return aggregated engagement stats keyed by author handle."""

        status_total = func.count(EngagementTask.id)
        status_completed = func.sum(
            case((EngagementTask.status == EngagementStatus.COMPLETED, 1), else_=0)
        )
        status_error = func.sum(
            case((EngagementTask.status == EngagementStatus.ERROR, 1), else_=0)
        )

        task_stmt = (
            select(
                EngagementTask.author_username,
                status_total.label("total"),
                status_completed.label("completed"),
                status_error.label("errors"),
                func.max(
                    case(
                        (EngagementTask.status == EngagementStatus.COMPLETED, EngagementTask.processed_at),
                        else_=None,
                    )
                ).label("last_completed_at"),
            )
            .where(EngagementTask.author_username.is_not(None))
            .group_by(EngagementTask.author_username)
        )

        outcome_follow = func.sum(case((EngagementOutcome.author_following == True, 1), else_=0))
        outcome_reply = func.sum(case((EngagementOutcome.replied == True, 1), else_=0))

        outcome_stmt = (
            select(
                EngagementOutcome.author_username,
                outcome_follow.label("follow_backs"),
                outcome_reply.label("replies_back"),
            )
            .where(EngagementOutcome.author_username.is_not(None))
            .group_by(EngagementOutcome.author_username)
        )

        task_rows = await session.execute(task_stmt)
        outcome_rows = await session.execute(outcome_stmt)

        stats: Dict[str, dict] = {}
        for username, total, completed, errors, last_completed_at in task_rows.all():
            handle = (username or "").lower()
            if not handle:
                continue
            stats[handle] = {
                "total": int(total or 0),
                "completed": int(completed or 0),
                "errors": int(errors or 0),
                "follow_backs": 0,
                "replies_back": 0,
                "last_completed_at": last_completed_at,
            }

        for username, follow_backs, replies_back in outcome_rows.all():
            handle = (username or "").lower()
            if not handle:
                continue
            bucket = stats.setdefault(
                handle,
                {
                    "total": 0,
                    "completed": 0,
                    "errors": 0,
                    "follow_backs": 0,
                    "replies_back": 0,
                    "last_completed_at": None,
                },
            )
            bucket["follow_backs"] = int(follow_backs or 0)
            bucket["replies_back"] = int(replies_back or 0)

        return stats
