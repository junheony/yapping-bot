"""Loader and helpers for priority project targeting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import yaml
from loguru import logger

from app.models import MindshareTarget

DEFAULT_PATH = Path("data/project_targets.yaml")
TIMELINE_CACHE_PATH = Path("data/project_timeline_cache.json")


@dataclass
class ProjectTarget:
    key: str
    name: str
    weight: float
    status: str
    onboarded: bool
    category: str = ""
    lookahead_days: int = 21
    max_mindshare: Optional[float] = None
    tickers: Set[str] = field(default_factory=set)
    official_handles: Set[str] = field(default_factory=set)
    community_handles: Set[str] = field(default_factory=set)
    official_handles_display: List[str] = field(default_factory=list)
    community_handles_display: List[str] = field(default_factory=list)
    handle_labels: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    search_queries: List[str] = field(default_factory=list)
    notes: str = ""
    upcoming_event_days: Optional[int] = None
    saturating: bool = False
    last_mindshare_score: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "weight": self.weight,
            "status": self.status,
            "onboarded": self.onboarded,
            "category": self.category,
            "lookahead_days": self.lookahead_days,
            "max_mindshare": self.max_mindshare,
            "tickers": sorted(self.tickers),
            "official_handles": sorted(self.official_handles),
            "community_handles": sorted(self.community_handles),
            "official_handles_display": self.official_handles_display,
            "community_handles_display": self.community_handles_display,
            "handle_labels": self.handle_labels,
            "keywords": self.keywords,
            "search_queries": self.search_queries,
            "notes": self.notes,
            "upcoming_event_days": self.upcoming_event_days,
            "saturating": self.saturating,
            "last_mindshare_score": self.last_mindshare_score,
        }


class ProjectPriorityLoader:
    """Parse project priority configuration and provide scoring helpers."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or DEFAULT_PATH
        self._projects: Dict[str, ProjectTarget] = {}
        self._handle_map: Dict[str, Tuple[str, str]] = {}
        self._ticker_map: Dict[str, str] = {}
        self._keyword_map: Dict[str, Set[str]] = {}
        self._search_queries: List[str] = []
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            logger.warning("Project priority file %s not found; skipping custom targeting", self.path)
            self._projects = {}
            self._handle_map = {}
            self._ticker_map = {}
            self._keyword_map = {}
            self._search_queries = []
            return

        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        projects: Dict[str, ProjectTarget] = {}
        handle_map: Dict[str, Tuple[str, str]] = {}
        ticker_map: Dict[str, str] = {}
        keyword_map: Dict[str, Set[str]] = {}
        search_queries: List[str] = []

        for item in raw.get("priority_projects", []):
            key = str(item.get("key") or "").strip().lower()
            if not key:
                continue
            official_handles_raw = [handle for handle in item.get("official_handles", []) if handle]
            community_handles_raw = [handle for handle in item.get("community_handles", []) if handle]
            official_handles_display = [self._format_handle(handle) for handle in official_handles_raw]
            community_handles_display = [self._format_handle(handle) for handle in community_handles_raw]
            handle_labels = self._merge_display_handles(official_handles_display, community_handles_display)
            target = ProjectTarget(
                key=key,
                name=item.get("name") or key.title(),
                weight=float(item.get("weight", 5.0)),
                status=item.get("status", "watch"),
                onboarded=bool(item.get("onboarded", False)),
                category=item.get("category", ""),
                lookahead_days=int(item.get("lookahead_days", 21)),
                max_mindshare=float(item["max_mindshare"]) if item.get("max_mindshare") is not None else None,
                tickers={ticker.upper() for ticker in item.get("tickers", [])},
                official_handles={self._normalise_handle(h) for h in official_handles_raw},
                community_handles={self._normalise_handle(h) for h in community_handles_raw},
                official_handles_display=official_handles_display,
                community_handles_display=community_handles_display,
                handle_labels=handle_labels,
                keywords=[kw.lower() for kw in item.get("keywords", []) if kw],
                search_queries=[query for query in item.get("search_queries", []) if query],
                notes=item.get("notes", ""),
            )
            projects[key] = target
            search_queries.extend(target.search_queries)
            for handle in target.official_handles:
                handle_map[handle] = (key, "official")
            for handle in target.community_handles:
                handle_map[handle] = (key, "community")
            for ticker in target.tickers:
                ticker_map[ticker.lower()] = key
            for keyword in target.keywords:
                keyword_map.setdefault(keyword, set()).add(key)

        self._projects = projects
        self._handle_map = handle_map
        self._ticker_map = ticker_map
        self._keyword_map = keyword_map
        self._search_queries = search_queries

        logger.info("Loaded %s priority projects (%s search queries)", len(projects), len(search_queries))

        cached_events = self._load_cached_events()
        if cached_events:
            logger.info("Applying %s cached timeline events", len(cached_events))
            self.update_timeline_events(cached_events)

    def _load_cached_events(self) -> List[dict]:
        if not TIMELINE_CACHE_PATH.exists():
            return []
        try:
            payload = json.loads(TIMELINE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("Failed to read timeline cache %s: %s", TIMELINE_CACHE_PATH, error)
            return []
        events = payload.get("timeline_events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return []
        return [event for event in events if isinstance(event, dict)]

    @staticmethod
    def _normalise_handle(handle: str) -> str:
        return handle.lower().lstrip("@")

    @staticmethod
    def _format_handle(handle: str) -> str:
        cleaned = handle.strip()
        return f"@{cleaned.lstrip('@')}" if cleaned else ""

    @classmethod
    def _merge_display_handles(cls, official: List[str], community: List[str]) -> List[str]:
        merged: List[str] = []
        seen: Set[str] = set()
        for handle in official + community:
            if not handle:
                continue
            normalised = cls._normalise_handle(handle)
            if normalised in seen:
                continue
            seen.add(normalised)
            merged.append(handle)
        return merged

    # Public helpers -----------------------------------------------------

    def projects(self) -> Sequence[ProjectTarget]:
        return list(self._projects.values())

    def official_handles(self) -> Set[str]:
        return {handle for handle, meta in self._handle_map.items() if meta[1] == "official"}

    def search_queries(self) -> List[str]:
        return list(dict.fromkeys(self._search_queries))

    def match_project_by_handle(self, handle: Optional[str]) -> Optional[Tuple[ProjectTarget, str]]:
        if not handle:
            return None
        meta = self._handle_map.get(self._normalise_handle(handle))
        if not meta:
            return None
        project_key, role = meta
        return self._projects.get(project_key), role

    def match_project_by_ticker(self, ticker: Optional[str]) -> Optional[ProjectTarget]:
        if not ticker:
            return None
        key = self._ticker_map.get(ticker.lower())
        if not key:
            return None
        return self._projects.get(key)

    def match_project_in_text(self, text: str) -> Optional[ProjectTarget]:
        lowered = text.lower() if text else ""
        candidates: Dict[str, int] = {}
        for keyword, project_keys in self._keyword_map.items():
            if keyword and keyword in lowered:
                for project_key in project_keys:
                    candidates[project_key] = candidates.get(project_key, 0) + 1
        if not candidates:
            return None
        # Choose the project with most matches; tie-break by weight.
        best_key = max(
            candidates.items(),
            key=lambda item: (item[1], self._projects[item[0]].weight),
        )[0]
        return self._projects.get(best_key)

    # Timeline and mindshare enrichment ---------------------------------

    def update_mindshare_targets(self, targets: Iterable[MindshareTarget]) -> None:
        """Update saturation heuristics based on recent mindshare scores."""

        for project in self._projects.values():
            project.saturating = False
            project.last_mindshare_score = None

        for target in targets:
            handle = self._normalise_handle(target.handle or "")
            if not handle:
                continue
            match = self._handle_map.get(handle)
            if not match:
                continue
            project_key, role = match
            project = self._projects.get(project_key)
            if not project:
                continue
            if role == "official":
                # official accounts inform saturation more strongly
                score = float(target.score or 0.0)
            else:
                score = float(target.score or 0.0) * 0.6
            project.last_mindshare_score = max(project.last_mindshare_score or 0.0, score)
            if project.max_mindshare is not None and score >= project.max_mindshare:
                project.saturating = True

    def update_timeline_events(self, events: Sequence[dict]) -> None:
        """Mark upcoming campaigns to boost pre-coverage.

        Args:
            events: Iterable of timeline event payloads captured from Gomtu (fields:
                projectTicker, startDate, endDate, status, category, isOpenEnded).
        """

        now = datetime.now(timezone.utc)
        for project in self._projects.values():
            project.upcoming_event_days = None

        for event in events:
            ticker = str(event.get("projectTicker") or "").lower()
            project = self.match_project_by_ticker(ticker)
            if not project:
                continue

            start_raw = event.get("startDate")
            if not start_raw:
                continue
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            except ValueError:
                logger.debug("Unrecognised startDate format for %s: %s", project.key, start_raw)
                continue
            delta = (start_dt - now).days
            if delta < 0:
                continue
            if project.upcoming_event_days is None or delta < project.upcoming_event_days:
                project.upcoming_event_days = delta

    # Scoring ------------------------------------------------------------

    def score_for_tweet(self, tweet: dict) -> Tuple[Optional[ProjectTarget], float, str]:
        """Return (project, bonus, reason_suffix) for a tweet."""

        author = tweet.get("author_username")
        text = tweet.get("text", "")
        project_match: Optional[ProjectTarget] = None
        reason_parts: List[str] = []

        handle_match = self.match_project_by_handle(author)
        if handle_match and handle_match[1] == "community":
            project_match = handle_match[0]
            reason_parts.append(f"handle:{project_match.key}")

        if not project_match:
            text_match = self.match_project_in_text(text)
            if text_match:
                project_match = text_match
                reason_parts.append(f"keyword:{project_match.key}")

        if not project_match:
            return None, 0.0, ""

        bonus = project_match.weight

        if project_match.upcoming_event_days is not None:
            if project_match.upcoming_event_days <= project_match.lookahead_days:
                # boost when event is within lookahead window
                bonus *= 1.25
                reason_parts.append(f"event:{project_match.upcoming_event_days}d")

        if project_match.saturating:
            bonus *= 0.6
            reason_parts.append("saturation")

        return project_match, bonus, "|".join(reason_parts)

    # Presentation helpers ----------------------------------------------

    def summary_payload(self) -> List[dict]:
        return [project.to_dict() for project in self._projects.values()]
