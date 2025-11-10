#!/usr/bin/env python3
"""Sync mindshare leaderboard data from publicly available Kaito ecosystem datasets."""

import asyncio
from datetime import datetime, timezone
from html import unescape
from typing import Dict, Iterable, List, Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy import inspect, text
from sqlalchemy.exc import NoSuchTableError

from app.database import SessionMaker, engine
from app.models import MindshareSource
from app.services.mindshare_repository import MindshareRepository

GOMTU_BASE = "https://gomtu.xyz/api/kaito"
INSIGHT_DURATIONS = ["7D", "30D"]
INSIGHT_CATEGORIES = ["preTGE", "postTGE"]

SUPABASE_BASE = "https://hpzpmhzejwvwiyacidvu.supabase.co/rest/v1"
SUPABASE_API_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhwenBtaHpland2d2l5YWNpZHZ1Iiwicm9s"
    "ZSI6ImFub24iLCJpYXQiOjE3NDY4MDM2ODgsImV4cCI6MjA2MjM3OTY4OH0.w_rk97jqMQH8gg1Ex29PRIq0IRpgd2rnHyYhqCYbn1c"
)
SUPABASE_HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Accept": "application/json",
    "Range": "0-1999",
    "Prefer": "count=estimated",
}

TARGET_USER_URL = "https://ppougod.netlify.app/target-user.json"
INNER_CIRCLE_PROJECT_NAME = "inner circle"


async def fetch_json(client: httpx.AsyncClient, url: str, **kwargs) -> dict:
    resp = await client.get(url, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def build_gomtu_records(leaderboard: Dict[int, dict], insights: List[dict]) -> List[dict]:
    aggregated: Dict[str, dict] = {}
    now = datetime.now(timezone.utc)

    for item in insights:
        project = leaderboard.get(item.get("projectId")) or {}
        ticker = (project.get("ticker") or "").strip()
        slug = (project.get("link") or "").strip()
        name = project.get("name")
        handle = ticker.lower() or slug.lower() or (name.lower().replace(" ", "-") if name else None)
        if not handle:
            continue

        current = aggregated.get(handle)
        new_score = item.get("mindshare_sum") or 0.0
        if current is None or new_score > current["score"]:
            aggregated[handle] = {
                "handle": handle,
                "display_name": name or ticker or slug or handle,
                "topic": project.get("category"),
                "score": new_score,
                "stats": {"project": project, "insight": item},
                "last_seen_at": now,
            }

    ranked = sorted(aggregated.values(), key=lambda x: x["score"], reverse=True)
    for idx, entry in enumerate(ranked, start=1):
        entry["rank"] = idx
    return ranked


async def ensure_inner_circle_column() -> None:
    async with engine.begin() as conn:

        def ensure(sync_conn) -> None:
            inspector = inspect(sync_conn)
            try:
                columns = {col["name"] for col in inspector.get_columns("mindshare_targets")}
            except NoSuchTableError:
                logger.warning("mindshare_targets table not found; skipping inner_circle migration")
                return
            if "inner_circle" not in columns:
                logger.info("Adding inner_circle column to mindshare_targets table (one-time migration)")
                sync_conn.execute(
                    text("ALTER TABLE mindshare_targets ADD COLUMN inner_circle BOOLEAN NOT NULL DEFAULT 0")
                )

        await conn.run_sync(ensure)


def normalise_display_name(value: str) -> str:
    if not value:
        return ""
    cleaned = unescape(value.replace("<br>", " ").replace("<br/>", " ").replace("\n", " "))
    return " ".join(cleaned.split()).strip()


def extract_inner_circle_handles(
    user_index: Dict[str, Iterable[dict]]
) -> Tuple[set[str], Dict[str, List[dict]], Dict[str, str]]:
    inner_circle = set()
    project_map: Dict[str, List[dict]] = {}
    display_map: Dict[str, str] = {}

    for raw_handle, entries in user_index.items():
        if not isinstance(entries, list):
            continue
        handle = raw_handle.lstrip("@").lower()
        project_map[handle] = entries
        display_map[handle] = raw_handle
        if any((item.get("project") or "").lower() == INNER_CIRCLE_PROJECT_NAME for item in entries):
            inner_circle.add(handle)

    return inner_circle, project_map, display_map


def build_supabase_records(
    entries: Iterable[dict],
    *,
    inner_circle_handles: set[str],
    project_map: Dict[str, List[dict]],
    display_map: Dict[str, str],
) -> List[dict]:
    now = datetime.now(timezone.utc)
    records: List[dict] = []

    for entry in entries:
        handle = (entry.get("x_handle") or "").lstrip("@").lower()
        if not handle:
            continue

        display_name = normalise_display_name(entry.get("name") or "") or display_map.get(handle, "")

        record = {
            "handle": handle,
            "display_name": display_name or handle,
            "topic": entry.get("network"),
            "rank": entry.get("rank"),
            "score": entry.get("mindshare") or 0.0,
            "stats": {
                "supabase": entry,
                "target_user_projects": project_map.get(handle, []),
            },
            "last_seen_at": now,
            "inner_circle": handle in inner_circle_handles,
        }
        records.append(record)

    return records


def compute_best_rank(entries: Iterable[dict]) -> Optional[int]:
    ranks: List[int] = []
    for item in entries:
        for key in ("7D", "30D", "3M"):
            value = item.get(key)
            if value is None:
                continue
            try:
                ranks.append(int(value))
            except (TypeError, ValueError):
                continue
    return min(ranks) if ranks else None


def compute_weighted_score(entries: Iterable[dict]) -> float:
    score = 0.0
    for item in entries:
        for key in ("7D_MS", "30D_MS", "3M_MS"):
            value = item.get(key)
            if value is None:
                continue
            try:
                score += float(value)
            except (TypeError, ValueError):
                continue
    return score


def build_target_user_records(
    project_map: Dict[str, List[dict]],
    display_map: Dict[str, str],
    inner_circle_handles: set[str],
) -> List[dict]:
    now = datetime.now(timezone.utc)
    records: List[dict] = []

    for handle, projects in project_map.items():
        if not projects:
            continue

        record = {
            "handle": handle,
            "display_name": display_map.get(handle, handle),
            "topic": "kaito_targets",
            "rank": compute_best_rank(projects),
            "score": compute_weighted_score(projects),
            "stats": {"projects": projects},
            "last_seen_at": now,
            "inner_circle": handle in inner_circle_handles,
        }
        records.append(record)

    return records


async def sync_gomtu(repo: MindshareRepository) -> None:
    async with httpx.AsyncClient(headers={"accept": "application/json"}) as client, SessionMaker() as session:
        leaderboard_resp = await fetch_json(client, f"{GOMTU_BASE}/leaderboard")
        leaderboard_map = {entry["id"]: entry for entry in leaderboard_resp.get("data", [])}

        all_insights: List[dict] = []
        for duration in INSIGHT_DURATIONS:
            for category in INSIGHT_CATEGORIES:
                data = await fetch_json(
                    client,
                    f"{GOMTU_BASE}/leaderboard-insights",
                    params={"duration": duration, "category": category},
                )
                payload = data.get("data", [])
                all_insights.extend(payload)
                logger.info("Fetched %s Gomtu insight records for %s %s", len(payload), duration, category)

        records = build_gomtu_records(leaderboard_map, all_insights)
        if not records:
            logger.warning("No mindshare records parsed from Gomtu responses")
            return

        await repo.upsert_targets(session, source=MindshareSource.GOMTU_STATS, targets=records)
        await session.commit()
        logger.info("Stored %s mindshare targets from Gomtu", len(records))


async def sync_kaito(repo: MindshareRepository) -> None:
    async with httpx.AsyncClient(headers={"accept": "application/json"}) as client, SessionMaker() as session:
        target_payload = await fetch_json(client, TARGET_USER_URL)
        user_index = target_payload.get("user_index") or {}
        inner_circle_handles, project_map, display_map = extract_inner_circle_handles(user_index)
        logger.info("Identified %s inner circle handles from target-user feed", len(inner_circle_handles))

        supabase_resp = await fetch_json(
            client,
            f"{SUPABASE_BASE}/leaderboard_entries",
            params={
                "select": "network,rank,name,x_handle,mindshare,smart_followers,real_followers,fetch_timestamp,x_profile_link",
                "order": "rank.asc.nullslast",
            },
            headers=SUPABASE_HEADERS,
        )

        supabase_records = build_supabase_records(
            supabase_resp,
            inner_circle_handles=inner_circle_handles,
            project_map=project_map,
            display_map=display_map,
        )
        if supabase_records:
            await repo.upsert_targets(session, source=MindshareSource.KAITO_SUPABASE, targets=supabase_records)
            logger.info("Stored %s mindshare targets from Kaito Supabase", len(supabase_records))
        else:
            logger.warning("No records parsed from Kaito Supabase feed")

        target_records = build_target_user_records(project_map, display_map, inner_circle_handles)
        if target_records:
            await repo.upsert_targets(session, source=MindshareSource.KAITO_TARGETS, targets=target_records)
            logger.info("Stored %s mindshare targets from Kaito target-user feed", len(target_records))
        else:
            logger.warning("No records built from target-user feed")

        await session.commit()


async def main() -> None:
    repo = MindshareRepository()
    await ensure_inner_circle_column()
    await sync_gomtu(repo)
    await sync_kaito(repo)


if __name__ == "__main__":
    asyncio.run(main())
