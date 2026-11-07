"""Read-only status routes for the yapping-bot API.

The default snapshot is empty on purpose. Production must pass its own sent log,
queue, and enforcement attestation after the live router file is snapshotted.
This module does not read credentials and does not post.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter

from app.quality.msg_status import build_hub_summary, build_tg_status

router = APIRouter(prefix="/api", tags=["status"])

_provider: Callable[[], dict] = lambda: {}


def set_snapshot_provider(provider: Optional[Callable[[], dict]]) -> None:
    global _provider
    _provider = provider or (lambda: {})


@router.get("/hub_summary")
def hub_summary() -> dict:
    return build_hub_summary(_provider())


@router.get("/tg_status")
def tg_status() -> dict:
    return build_tg_status(_provider())
