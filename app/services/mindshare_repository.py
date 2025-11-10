"""Repository helpers for mindshare targets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MindshareSource, MindshareTarget


class MindshareRepository:
    """Store and retrieve mindshare leaderboard data."""

    @staticmethod
    async def upsert_targets(
        session: AsyncSession,
        *,
        source: str,
        targets: Iterable[dict],
    ) -> None:
        # remove existing for source to avoid duplicates
        await session.execute(delete(MindshareTarget).where(MindshareTarget.source == source))

        now = datetime.now(timezone.utc)
        for entry in targets:
            target = MindshareTarget(
                source=source,
                rank=entry.get("rank"),
                handle=entry.get("handle"),
                display_name=entry.get("display_name"),
                topic=entry.get("topic"),
                score=entry.get("score"),
                stats=entry.get("stats"),
                last_seen_at=entry.get("last_seen_at", now),
                inner_circle=bool(entry.get("inner_circle", False)),
            )
            session.add(target)

        await session.flush()

    @staticmethod
    async def list_targets(session: AsyncSession, *, limit: int = 50) -> List[MindshareTarget]:
        stmt = select(MindshareTarget).order_by(MindshareTarget.rank.asc().nullslast(), MindshareTarget.score.desc()).limit(
            limit
        )
        result = await session.execute(stmt)
        return list(result.scalars())
