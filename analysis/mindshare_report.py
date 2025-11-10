"""Generate a human-readable priority report for mindshare targets."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from sqlalchemy import select

from app.database import SessionMaker
from app.models import EngagementOutcome, EngagementTask, MindshareTarget

REPORT_PATH = Path("analysis/mindshare_priority_report.md")


@dataclass
class TargetSnapshot:
    target: MindshareTarget
    total_tasks: int
    completed_tasks: int
    error_tasks: int
    follow_backs: int
    replies_back: int
    latest_outcome: EngagementOutcome | None
    priority_score: float
    smart_followers: int
    real_followers: int
    smart_engagements: int
    impressions: int
    keywords: List[str]


async def _load_data() -> tuple[List[MindshareTarget], List[EngagementTask], List[EngagementOutcome]]:
    async with SessionMaker() as session:
        target_rows = await session.execute(select(MindshareTarget))
        task_rows = await session.execute(select(EngagementTask))
        outcome_rows = await session.execute(select(EngagementOutcome))
        return (
            list(target_rows.scalars()),
            list(task_rows.scalars()),
            list(outcome_rows.scalars()),
        )


def _index_tasks(tasks: Iterable[EngagementTask]) -> Dict[str, List[EngagementTask]]:
    bucket: Dict[str, List[EngagementTask]] = defaultdict(list)
    for task in tasks:
        handle = (task.author_username or "").lower()
        if not handle:
            continue
        bucket[handle].append(task)
    return bucket


def _index_outcomes(outcomes: Iterable[EngagementOutcome]) -> Dict[int, EngagementOutcome]:
    return {outcome.task_id: outcome for outcome in outcomes}


def _priority_score(target: MindshareTarget, now: datetime, total_tasks: int) -> float:
    score = float(target.score or 0.0)
    last_seen = target.last_seen_at
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    else:
        last_seen = last_seen.astimezone(timezone.utc)
    days_since_seen = max((now - last_seen).total_seconds() / 86400.0, 0.0)
    recency_bonus = max(0.0, 15.0 - days_since_seen) * 0.6
    workload_bonus = min(total_tasks, 12) * 0.4
    inner_circle_bonus = 5.0 if target.inner_circle else 0.0
    stats = target.stats or {}
    supabase_stats = stats.get("supabase") or {}
    insight_stats = stats.get("insight") or {}
    smart_followers = 0.0
    impressions = 0.0
    try:
        smart_followers = float(
            supabase_stats.get("smart_followers")
            or insight_stats.get("smart_follower_sum")
            or 0.0
        )
    except (TypeError, ValueError):
        smart_followers = 0.0
    try:
        impressions = float(insight_stats.get("impressions") or 0.0)
    except (TypeError, ValueError):
        impressions = 0.0
    smart_bonus = min(smart_followers / 400.0, 25.0)
    impression_bonus = min(impressions / 1_000_000.0, 12.0)
    return score * 0.6 + recency_bonus + workload_bonus + inner_circle_bonus + smart_bonus + impression_bonus


def build_snapshots(
    targets: List[MindshareTarget],
    tasks: List[EngagementTask],
    outcomes: List[EngagementOutcome],
) -> List[TargetSnapshot]:
    now = datetime.now(timezone.utc)
    tasks_by_handle = _index_tasks(tasks)
    outcome_by_task = _index_outcomes(outcomes)

    snapshots: List[TargetSnapshot] = []
    for target in targets:
        handle = (target.handle or "").lower()
        related_tasks = tasks_by_handle.get(handle, [])
        total_tasks = len(related_tasks)
        completed_tasks = sum(1 for task in related_tasks if task.status == "completed")
        error_tasks = sum(1 for task in related_tasks if task.status == "error")
        follow_backs = 0
        replies_back = 0
        latest_outcome = None
        stats_payload = target.stats or {}
        supabase_stats = stats_payload.get("supabase") or {}
        insight_stats = stats_payload.get("insight") or {}
        project_stats = stats_payload.get("project") or {}

        def _coerce_int(value) -> int:
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        smart_followers = _coerce_int(
            supabase_stats.get("smart_followers") or insight_stats.get("smart_follower_sum")
        )
        real_followers = _coerce_int(supabase_stats.get("real_followers"))
        smart_engagements = _coerce_int(insight_stats.get("smart_engagements"))
        impressions = _coerce_int(insight_stats.get("impressions"))
        project_keywords_raw = project_stats.get("keywords") or project_stats.get("tags") or []
        if isinstance(project_keywords_raw, str):
            keywords = [kw.strip() for kw in project_keywords_raw.split(",") if kw.strip()]
        else:
            keywords = [str(kw).strip() for kw in project_keywords_raw if kw]

        for task in related_tasks:
            outcome = outcome_by_task.get(task.id)
            if not outcome:
                continue
            latest_outcome = outcome if not latest_outcome or outcome.checked_at > latest_outcome.checked_at else latest_outcome
            if outcome.author_following:
                follow_backs += 1
            if outcome.replied:
                replies_back += 1

        snapshots.append(
            TargetSnapshot(
                target=target,
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                error_tasks=error_tasks,
                follow_backs=follow_backs,
                replies_back=replies_back,
                latest_outcome=latest_outcome,
                priority_score=_priority_score(target, now, total_tasks),
                smart_followers=smart_followers,
                real_followers=real_followers,
                smart_engagements=smart_engagements,
                impressions=impressions,
                keywords=keywords,
            )
        )

    snapshots.sort(key=lambda snap: snap.priority_score, reverse=True)
    return snapshots


def render_report(snapshots: List[TargetSnapshot]) -> str:
    if not snapshots:
        return "No mindshare targets stored yet. Run scripts/mindshare_sync.py first."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_source = Counter(s.target.source for s in snapshots)

    lines: List[str] = []
    lines.append("# Mindshare Priority Roll-up")
    lines.append(f"_Generated {now}_\n")
    lines.append("## Snapshot")
    for source, count in sorted(by_source.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- **{source}** · {count} targets")

    lines.append("\n## Top Targets")
    for idx, snap in enumerate(snapshots[:20], start=1):
        target = snap.target
        last_seen = target.last_seen_at.strftime("%Y-%m-%d %H:%M")
        inner = " · Inner circle" if target.inner_circle else ""
        lines.append(
            f"{idx}. **@{target.handle}** ({target.display_name or target.handle})\n"
            f"   - Priority score: {snap.priority_score:.1f}{inner}\n"
            f"   - Source: {target.source} · Rank: {target.rank or '—'} · Topic: {target.topic or '—'}\n"
            f"   - Last seen: {last_seen}\n"
            f"   - Engagement: {snap.completed_tasks}/{snap.total_tasks} completed, {snap.error_tasks} errors"
        )
        if snap.smart_followers or snap.real_followers:
            ratio = 0.0
            if snap.real_followers:
                ratio = (snap.smart_followers / snap.real_followers) * 100.0
            smart_line = f"   - Smart CT base: {snap.smart_followers:,}"
            if snap.real_followers:
                smart_line += f" ({ratio:.1f}% of {snap.real_followers:,})"
            lines.append(smart_line)
        if snap.impressions or snap.smart_engagements:
            impression_text = f"impressions {snap.impressions:,}" if snap.impressions else None
            engagement_text = f"smart engagements {snap.smart_engagements:,}" if snap.smart_engagements else None
            stats_line = " · ".join(filter(None, [impression_text, engagement_text]))
            if stats_line:
                lines.append(f"   - Recent data: {stats_line}")
        if snap.follow_backs or snap.replies_back:
            lines.append(
                f"   - Outcomes: follow-back {snap.follow_backs}, replied {snap.replies_back}"
            )
        if snap.latest_outcome and snap.latest_outcome.notes:
            lines.append(f"   - Notes: {snap.latest_outcome.notes}")
        if snap.keywords:
            preview = ", ".join(snap.keywords[:6])
            lines.append(f"   - Keyword cues: {preview}")
        lines.append("")

    lines.append(
        "---\nThis list is prioritised with a bias towards recent sightings, inner-circle "
        "targets, and handles that triggered engagement tasks. Adjust heuristics in "
        "`analysis/mindshare_report.py` as you learn more from human feedback."
    )

    return "\n".join(lines)


def save_report(markdown: str) -> None:
    REPORT_PATH.write_text(markdown, encoding="utf-8")


def main() -> None:
    targets, tasks, outcomes = asyncio.run(_load_data())
    snapshots = build_snapshots(targets, tasks, outcomes)
    report = render_report(snapshots)
    save_report(report)
    print(f"Report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
