"""Keyword filtering utilities."""

from typing import Tuple

from app.config import settings
from app.schemas import YapPayload


def apply_filters(payload: YapPayload) -> Tuple[bool, str, bool]:
    """Return (allowed, reason, manual_review) based on keyword rules."""

    corpus = f"{payload.title} {' '.join(payload.tags)}".lower()

    whitelist_hit = any(keyword.lower() in corpus for keyword in settings.whitelist_keywords)
    if not whitelist_hit:
        return False, "No whitelist keyword matched.", False

    blacklist_hit = next((kw for kw in settings.blacklist_keywords if kw.lower() in corpus), None)
    if blacklist_hit:
        return False, f"Blocked by blacklist keyword: {blacklist_hit}", False

    manual_hit = any(keyword.lower() in corpus for keyword in settings.manual_review_keywords)

    return True, "Approved.", manual_hit
