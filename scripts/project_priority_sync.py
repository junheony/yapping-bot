#!/usr/bin/env python3
"""Sync priority project metadata (timeline + watchlists)."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx
from loguru import logger

from app.services.project_priority_loader import ProjectPriorityLoader

TARGET_USER_URL = "https://ppougod.netlify.app/target-user.json"
TIMELINE_URL = "https://gomtu.xyz/timeline"
CACHE_PATH = Path("data/project_timeline_cache.json")


async def fetch_target_user_payload() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(TARGET_USER_URL)
        resp.raise_for_status()
        return resp.json()


async def capture_timeline_events() -> List[dict]:
    """Attempt to capture timeline events by loading the Gomtu timeline page.

    Uses Playwright if available; falls back to an empty list otherwise.
    """

    try:
        from playwright.async_api import async_playwright
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("Playwright not installed; skipping timeline capture")
        return []

    events: List[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        async def handle_response(response) -> None:
            url = response.url
            if "timeline" not in url and "campaign" not in url:
                return
            try:
                data = await response.json()
            except Exception:
                return
            if isinstance(data, list):
                candidates = data
            elif isinstance(data, dict):
                candidates = []
                for key in ("events", "timeline", "data", "campaigns", "items"):
                    value = data.get(key)
                    if isinstance(value, list):
                        candidates.extend(value)
            else:
                candidates = []

            if candidates:
                logger.debug("Captured %s timeline entries from %s", len(candidates), url)
                for candidate in candidates:
                    if isinstance(candidate, dict):
                        events.append(candidate)

        page.on("response", lambda response: asyncio.create_task(handle_response(response)))
        await page.goto(TIMELINE_URL, wait_until="networkidle")
        await asyncio.sleep(4)
        await browser.close()

    deduped: List[dict] = []
    seen = set()
    for event in events:
        key = (
            event.get("campaignId"),
            event.get("projectTicker"),
            event.get("startDate"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)

    logger.info("Timeline capture produced %s unique events", len(deduped))
    return deduped


def write_cache(payload: Dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote timeline cache to %s", CACHE_PATH)


async def main() -> None:
    loader = ProjectPriorityLoader()

    target_payload = await fetch_target_user_payload()
    logger.info("Fetched target-user mapping with %s handles", len(target_payload.get("user_index", {})))

    timeline_events = await capture_timeline_events()
    loader.update_timeline_events(timeline_events)

    write_cache(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timeline_events": timeline_events,
        }
    )

    summary = []
    for project in loader.projects():
        summary.append(
            {
                "key": project.key,
                "upcoming_event_days": project.upcoming_event_days,
                "saturating": project.saturating,
                "last_mindshare_score": project.last_mindshare_score,
            }
        )
    logger.info("Project timeline summary: %s", summary)


if __name__ == "__main__":
    asyncio.run(main())
