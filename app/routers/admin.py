"""Admin dashboard routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import MindshareTarget, PostRecord, PostStatus
from app.services.pipeline import YapProcessor
from app.services.repository import PostRepository
from app.services.engagement_repository import EngagementRepository
from app.services.mindshare_repository import MindshareRepository
from app.services.project_priority_loader import ProjectPriorityLoader
from app.services.usage_repository import UsageRepository
from app.services.engagement_outcome_repository import EngagementOutcomeRepository
from app.runtime import (
    engagement_worker_running,
    engagement_insights_running,
    start_engagement_worker,
    stop_engagement_worker,
)
from app.config import settings

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(prefix="/admin", tags=["admin"])


def _serialize_queue_item(record: PostRecord) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    age_minutes = None
    queued_at = record.queued_at
    aware_queued = None
    if queued_at:
        aware_queued = queued_at
        if queued_at.tzinfo is None:
            aware_queued = queued_at.replace(tzinfo=timezone.utc)
        elif queued_at.tzinfo != timezone.utc:
            aware_queued = queued_at.astimezone(timezone.utc)
        delta = now - aware_queued
        age_minutes = int(delta.total_seconds() // 60)
    return {
        "hash": record.hash,
        "title": record.title,
        "status": record.status,
        "manual_review_required": record.manual_review_required,
        "approved": record.approved,
        "queued_at": aware_queued.isoformat() if queued_at else "",
        "attempt_count": record.attempt_count,
        "priority": record.priority,
        "source_type": record.source_type,
        "tags": record.tags.split(",") if record.tags else [],
        "is_stale": bool(age_minutes is not None and age_minutes >= 720),
        "queued_age_minutes": age_minutes,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    post_repo = PostRepository()
    engagement_repo = EngagementRepository()
    mindshare_repo = MindshareRepository()
    priority_loader = ProjectPriorityLoader()
    usage_repo = UsageRepository(session)
    queue_records = await post_repo.list_pending(session, manual_only=None, limit=100)
    log_entries = await post_repo.get_recent_logs(session, limit=30)
    summary = await post_repo.get_summary_stats(session)
    engagement_tasks = await engagement_repo.list_recent_tasks(session, limit=30)
    engagement_logs = await engagement_repo.recent_logs(session, limit=40)
    engagement_summary = await engagement_repo.get_summary(session)
    handle_stats = await engagement_repo.handle_stats(session)
    mindshare_targets = await mindshare_repo.list_targets(session, limit=36)
    priority_projects = priority_loader.summary_payload()
    priority_lookup = {item["key"]: item for item in priority_projects}
    engagement_buckets = _build_engagement_buckets(engagement_tasks, priority_lookup)
    usage_summary = await _usage_summary(usage_repo)
    outcome_repo = EngagementOutcomeRepository()
    engagement_outcomes = await outcome_repo.recent(session, limit=25)

    context = {
        "request": request,
        "title": "Yapping Admin",
        "items": [_serialize_queue_item(record) for record in queue_records],
        "logs": log_entries,
        "summary": summary,
        "engagement_tasks": engagement_tasks,
        "engagement_logs": engagement_logs,
        "engagement_summary": engagement_summary,
        "mindshare_targets": mindshare_targets,
        "priority_projects": priority_projects,
        "engagement_buckets": engagement_buckets,
        "api_usage": usage_summary,
        "engagement_running": engagement_worker_running(),
        "insights_running": engagement_insights_running(),
        "engagement_outcomes": engagement_outcomes,
        "mindshare_priority": _build_mindshare_priority(mindshare_targets, handle_stats),
    }
    return templates.TemplateResponse("dashboard.html", context)


@router.get("/partials/queue", response_class=HTMLResponse)
async def queue_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    repo = PostRepository()
    queue_records = await repo.list_pending(session, manual_only=None, limit=100)
    context = {
        "request": request,
        "items": [_serialize_queue_item(record) for record in queue_records],
    }
    return templates.TemplateResponse("partials/queue_cards.html", context)


@router.get("/partials/logs", response_class=HTMLResponse)
async def logs_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    repo = PostRepository()
    log_entries = await repo.get_recent_logs(session, limit=30)
    context = {"request": request, "logs": log_entries}
    return templates.TemplateResponse("partials/log_cards.html", context)


@router.get("/partials/metrics", response_class=HTMLResponse)
async def metrics_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    post_repo = PostRepository()
    engagement_repo = EngagementRepository()
    mindshare_repo = MindshareRepository()
    priority_loader = ProjectPriorityLoader()
    summary = await post_repo.get_summary_stats(session)
    engagement_summary = await engagement_repo.get_summary(session)
    mindshare_targets = await mindshare_repo.list_targets(session, limit=12)
    context = {
        "request": request,
        "summary": summary,
        "engagement_summary": engagement_summary,
        "mindshare_targets": mindshare_targets,
        "priority_projects": priority_loader.summary_payload(),
    }
    return templates.TemplateResponse("partials/metrics.html", context)


@router.get("/partials/usage", response_class=HTMLResponse)
async def usage_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    usage_repo = UsageRepository(session)
    context = {
        "request": request,
        "api_usage": await _usage_summary(usage_repo),
        "engagement_running": engagement_worker_running(),
        "insights_running": engagement_insights_running(),
    }
    return templates.TemplateResponse("partials/api_usage.html", context)


@router.get("/partials/mindshare-priority", response_class=HTMLResponse)
async def mindshare_priority_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    mindshare_repo = MindshareRepository()
    engagement_repo = EngagementRepository()
    targets = await mindshare_repo.list_targets(session, limit=50)
    handle_stats = await engagement_repo.handle_stats(session)
    context = {
        "request": request,
        "mindshare_priority": _build_mindshare_priority(targets, handle_stats),
    }
    return templates.TemplateResponse("partials/mindshare_priority.html", context)


@router.post("/engagement/start", response_class=HTMLResponse)
async def engagement_start(request: Request) -> HTMLResponse:
    start_engagement_worker()
    context = {"request": request, "engagement_running": True}
    return templates.TemplateResponse("partials/engagement_control.html", context)


@router.post("/engagement/stop", response_class=HTMLResponse)
async def engagement_stop(request: Request) -> HTMLResponse:
    await stop_engagement_worker()
    context = {"request": request, "engagement_running": False}
    return templates.TemplateResponse("partials/engagement_control.html", context)


@router.get("/partials/engagement", response_class=HTMLResponse)
async def engagement_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    repo = EngagementRepository()
    tasks = await repo.list_recent_tasks(session, limit=30)
    priority_loader = ProjectPriorityLoader()
    priority_projects = priority_loader.summary_payload()
    priority_lookup = {item["key"]: item for item in priority_projects}
    buckets = _build_engagement_buckets(tasks, priority_lookup)
    context = {
        "request": request,
        "engagement_tasks": tasks,
        "engagement_buckets": buckets,
        "priority_projects": priority_projects,
    }
    return templates.TemplateResponse("partials/engagement_table.html", context)


@router.get("/partials/engagement-logs", response_class=HTMLResponse)
async def engagement_logs_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    repo = EngagementRepository()
    logs = await repo.recent_logs(session, limit=40)
    context = {"request": request, "engagement_logs": logs}
    return templates.TemplateResponse("partials/engagement_log_cards.html", context)


@router.get("/partials/mindshare", response_class=HTMLResponse)
async def mindshare_partial(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    repo = MindshareRepository()
    targets = await repo.list_targets(session, limit=50)
    context = {"request": request, "mindshare_targets": targets}
    return templates.TemplateResponse("partials/mindshare_board.html", context)


@router.get("/partials/priority-projects", response_class=HTMLResponse)
async def priority_projects_partial(request: Request) -> HTMLResponse:
    loader = ProjectPriorityLoader()
    context = {"request": request, "priority_projects": loader.summary_payload()}
    return templates.TemplateResponse("partials/priority_projects.html", context)


def _build_engagement_buckets(tasks: List[Any], priority_lookup: Dict[str, dict]) -> List[dict]:
    buckets: List[dict] = []
    bucket_map: Dict[str, dict] = {}

    for task in tasks:
        reason_parts = [part.strip() for part in (task.reason or "").split("|") if part.strip()]
        project_key: Optional[str] = None
        tags: List[str] = []

        for part in reason_parts:
            if part.startswith("project:"):
                project_key = part.split(":", 1)[1]
            else:
                tags.append(part)

        bucket_key = project_key or "__other__"
        bucket = bucket_map.get(bucket_key)
        if bucket is None:
            bucket = {
                "project_key": project_key,
                "project": priority_lookup.get(project_key),
                "tasks": [],
            }
            bucket_map[bucket_key] = bucket
            buckets.append(bucket)

        bucket["tasks"].append({"task": task, "tags": tags})

    return buckets


async def _usage_summary(repo: UsageRepository) -> Dict[str, Any]:
    (window_start, window_end), records = await repo.list_window_stats(
        "twitter", reset_day=settings.twitter_usage_reset_day
    )
    record_map = {record.metric: record for record in records}

    posts_stat = record_map.get("search_posts_pulled")
    requests_stat = record_map.get("search_requests")

    cap = settings.twitter_search_monthly_cap
    posts_raw = posts_stat.value if posts_stat else 0
    baseline_posts = settings.twitter_usage_baseline_posts
    posts_value = max(0, posts_raw - baseline_posts)
    percent = min(100.0, (posts_value / cap * 100.0)) if cap else None

    metrics = [
        {
            "key": "search_posts_pulled",
            "label": "Posts Pulled",
            "value": posts_value,
            "cap": cap,
            "percent": round(percent, 2) if percent is not None else None,
            "meta": {
                "raw": posts_raw,
                "baseline": baseline_posts,
                "window": posts_value,
                **(((posts_stat.meta or {}) if posts_stat else {})),
            },
            "updated_at": posts_stat.updated_at if posts_stat else None,
        },
        {
            "key": "search_requests",
            "label": "Search Requests",
            "value": max(0, (requests_stat.value if requests_stat else 0) - settings.twitter_usage_baseline_requests),
            "cap": None,
            "percent": None,
            "meta": {
                "raw": requests_stat.value if requests_stat else 0,
                "baseline": settings.twitter_usage_baseline_requests,
                **(((requests_stat.meta or {}) if requests_stat else {})),
            },
            "updated_at": requests_stat.updated_at if requests_stat else None,
        },
    ]

    return {
        "provider": "twitter",
        "window_start": window_start,
        "window_end": window_end,
        "metrics": metrics,
    }


def _build_mindshare_priority(targets: List[MindshareTarget], handle_stats: Dict[str, dict]) -> List[dict]:
    now = datetime.now(timezone.utc)
    cards: List[dict] = []
    for target in targets:
        handle = (target.handle or "").lower()
        stats = handle_stats.get(handle, {})
        cards.append(
            {
                "handle": target.handle,
                "display_name": target.display_name or target.handle,
                "priority_score": _compute_priority_score(target, now, stats.get("total", 0)),
                "last_seen_at": target.last_seen_at,
                "source": target.source,
                "rank": target.rank,
                "topic": target.topic,
                "inner_circle": target.inner_circle,
                "stats": {
                    "total": stats.get("total", 0),
                    "completed": stats.get("completed", 0),
                    "errors": stats.get("errors", 0),
                    "follow_backs": stats.get("follow_backs", 0),
                    "replies_back": stats.get("replies_back", 0),
                },
            }
        )

    cards.sort(key=lambda item: item["priority_score"], reverse=True)
    return cards[:20]


def _compute_priority_score(target: MindshareTarget, now: datetime, total_tasks: int) -> float:
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
    return score * 0.6 + recency_bonus + workload_bonus + inner_circle_bonus


@router.post("/queue/{hash_value}/approve", response_class=HTMLResponse)
async def approve_queue_item(
    request: Request,
    hash_value: str,
    process_immediately: bool = Query(True),
    session: AsyncSession = Depends(get_session),
) -> Response:
    repo = PostRepository()
    record = await repo.get_by_hash(session, hash_value)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    processor = YapProcessor(session)

    if process_immediately:
        await processor.process_record(record)
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await repo.approve_post(session, record)
    await repo.set_manual_review(session, record, False)
    await repo.log_event(session, record, "approved", "Manual approval via dashboard.")
    await session.commit()
    record = await repo.get_by_hash(session, hash_value)
    if not record:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    context = {"request": request, "item": _serialize_queue_item(record)}
    return templates.TemplateResponse("partials/queue_card.html", context)


@router.post("/queue/{hash_value}/skip", response_class=HTMLResponse)
async def skip_queue_item(
    hash_value: str,
    reason: Optional[str] = Query(default="Manually skipped from dashboard."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    repo = PostRepository()
    record = await repo.get_by_hash(session, hash_value)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    await repo.mark_status(session, record, status=PostStatus.SKIPPED, error_msg=reason, clear_next_attempt=True)
    await repo.log_event(session, record, "skipped", reason)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
