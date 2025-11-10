"""Webhook ingestion and queue management router."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import PostStatus
from app.schemas import QueueItem, YapPayload, YapResponse
from app.services.pipeline import YapProcessor
from app.services.repository import PostRepository

router = APIRouter(prefix="/yap", tags=["yapping"])


@router.post("/push", response_model=YapResponse, status_code=status.HTTP_202_ACCEPTED)
async def push_yap(payload: YapPayload, session: AsyncSession = Depends(get_session)) -> YapResponse:
    """Ingest a new payload and trigger processing."""

    processor = YapProcessor(session)
    try:
        response = await processor.handle(payload)
        await session.commit()
        return response
    except RuntimeError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/queue", response_model=List[QueueItem])
async def list_queue(
    manual_only: Optional[bool] = Query(default=None, description="Filter by manual review requirement."),
    session: AsyncSession = Depends(get_session),
) -> List[QueueItem]:
    """Return pending queue items."""

    repo = PostRepository()
    records = await repo.list_pending(session, manual_only=manual_only)
    return [
        QueueItem(
            hash=record.hash,
            title=record.title,
            status=record.status,
            manual_review_required=record.manual_review_required,
            approved=record.approved,
            queued_at=record.queued_at,
            attempt_count=record.attempt_count,
            priority=record.priority,
            source_type=record.source_type,
            tags=record.tags.split(",") if record.tags else [],
        )
        for record in records
    ]


@router.post("/queue/{hash_value}/approve", response_model=YapResponse)
async def approve_item(
    hash_value: str,
    process_immediately: bool = Query(
        default=False,
        description="If true, process the item right away instead of leaving it for the queue worker.",
    ),
    session: AsyncSession = Depends(get_session),
) -> YapResponse:
    """Approve a pending item and optionally process immediately."""

    repo = PostRepository()
    record = await repo.get_by_hash(session, hash_value)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    await repo.approve_post(session, record)
    await repo.set_manual_review(session, record, False)
    await repo.log_event(session, record, "approved", "Manual approval granted.")

    if not process_immediately:
        await session.commit()
        return YapResponse(hash=hash_value, status=record.status, message="Approved; scheduled for queue.")

    processor = YapProcessor(session)
    response = await processor.process_record(record)
    await session.commit()
    return response


@router.post("/queue/{hash_value}/skip", response_model=YapResponse)
async def skip_item(
    hash_value: str,
    reason: str = Query(default="Manually skipped."),
    session: AsyncSession = Depends(get_session),
) -> YapResponse:
    """Skip or cancel a pending item."""

    repo = PostRepository()
    record = await repo.get_by_hash(session, hash_value)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    await repo.mark_status(session, record, status=PostStatus.SKIPPED, error_msg=reason, clear_next_attempt=True)
    await repo.log_event(session, record, "skipped", reason)
    await session.commit()
    return YapResponse(hash=hash_value, status=PostStatus.SKIPPED, message=reason)
