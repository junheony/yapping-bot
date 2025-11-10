"""Helpers for tracking external API usage windows."""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import APIUsageStat


class UsageRepository:
    """Persist and query aggregated usage statistics."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def increment(
        self,
        provider: str,
        metric: str,
        amount: int,
        *,
        reset_day: int,
        meta: Optional[dict] = None,
        at: Optional[datetime] = None,
    ) -> APIUsageStat:
        """Increase usage for the current billing window."""

        now = at or datetime.now(timezone.utc)
        window_start, window_end = self._current_window(reset_day, now)
        if amount <= 0:
            return await self._get_window_record(provider, metric, window_start, window_end)
        existing = await self._get_window_record(provider, metric, window_start, window_end)
        if existing is None:
            record = APIUsageStat(
                provider=provider,
                metric=metric,
                window_start=window_start,
                window_end=window_end,
                value=amount,
                meta=meta or {},
            )
            self.session.add(record)
            await self.session.flush()
            return record

        existing.value += amount
        if meta:
            merged = dict(existing.meta or {})
            merged.update(meta)
            existing.meta = merged
        await self.session.flush()
        return existing

    async def get_window_summary(
        self, provider: str, metric: str, *, reset_day: int, at: Optional[datetime] = None
    ) -> Tuple[Tuple[datetime, datetime], Optional[APIUsageStat]]:
        """Return the usage window and the stored statistic (if any)."""

        now = at or datetime.now(timezone.utc)
        window_start, window_end = self._current_window(reset_day, now)
        record = await self._get_window_record(provider, metric, window_start, window_end)
        return (window_start, window_end), record

    async def list_window_stats(
        self, provider: str, *, reset_day: int, at: Optional[datetime] = None
    ) -> Tuple[Tuple[datetime, datetime], List[APIUsageStat]]:
        """Return all metrics captured for the current window."""

        now = at or datetime.now(timezone.utc)
        window_start, window_end = self._current_window(reset_day, now)
        stmt = (
            select(APIUsageStat)
            .where(
                APIUsageStat.provider == provider,
                APIUsageStat.window_start == window_start,
                APIUsageStat.window_end == window_end,
            )
            .order_by(APIUsageStat.metric.asc())
        )
        result = await self.session.execute(stmt)
        return (window_start, window_end), list(result.scalars())

    async def _get_window_record(
        self, provider: str, metric: str, window_start: datetime, window_end: datetime
    ) -> Optional[APIUsageStat]:
        stmt = select(APIUsageStat).where(
            APIUsageStat.provider == provider,
            APIUsageStat.metric == metric,
            APIUsageStat.window_start == window_start,
            APIUsageStat.window_end == window_end,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    def _current_window(reset_day: int, now: datetime) -> Tuple[datetime, datetime]:
        """Compute the active billing window using the given reset day (1-28)."""

        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        reset_day = max(1, min(reset_day, 28))

        start_year = now.year
        start_month = now.month
        cycle_start_day = min(reset_day, monthrange(start_year, start_month)[1])
        cycle_start = datetime(start_year, start_month, cycle_start_day, tzinfo=timezone.utc)
        if now < cycle_start:
            # rollover to previous month
            if start_month == 1:
                start_month = 12
                start_year -= 1
            else:
                start_month -= 1
            cycle_start_day = min(reset_day, monthrange(start_year, start_month)[1])
            cycle_start = datetime(start_year, start_month, cycle_start_day, tzinfo=timezone.utc)

        end_year = cycle_start.year
        end_month = cycle_start.month + 1
        if end_month == 13:
            end_month = 1
            end_year += 1
        cycle_end_day = min(reset_day, monthrange(end_year, end_month)[1])
        cycle_end = datetime(end_year, end_month, cycle_end_day, tzinfo=timezone.utc)

        return cycle_start, cycle_end
