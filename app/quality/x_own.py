"""English original posts for @cblabx.

This module drafts text. It does not call the X API, read cookies, sign XHunt,
or drive a browser. The existing Chrome extension executor stays outside this file;
publish() always refuses so tests and imports cannot post.
"""
from __future__ import annotations

import re

from .tg_news import assess_newsworthiness, story_keys, duplicate_reason, sanitize_source_url

ACCOUNT = "@cblabx"
X_API_ENABLED = False


class OwnContentPublishBlocked(RuntimeError):
    """Raised for every publish attempt. The X API lane stays off."""


def publish(*_args, **_kwargs):
    raise OwnContentPublishBlocked(
        "X API is off. This module does not publish, and it does not call the Chrome extension."
    )


_OPINION_RE = re.compile(
    r"\b(?:i think|i believe|in my opinion|in my view|game[- ]changer|bullish|bearish|must[- ]use)\b|"
    r"\b(?:i|we)\s+(?:tried|tested|used)\b|in my testing|개인적으로",
    re.I,
)
_QT_RE = re.compile(r"^\s*(?:qt\b|q/?t\b|quote tweet\b|translation\b|번역)", re.I)
_FUNC = {
    "a", "an", "the", "of", "to", "and", "or", "for", "from", "with", "on", "in", "that", "this",
    "it", "is", "are", "was", "were", "be", "as", "by", "at", "into", "over", "after", "before",
    "not", "no", "but", "if", "than", "then", "so", "you", "your", "our", "their", "can", "now",
    "because", "users", "user", "what", "when", "who", "how", "new", "just", "like", "about",
    "that", "matters", "matter", "important", "useful", "means", "change", "changed", "lets",
    "let", "able", "without", "same", "asking", "reader", "readers", "from", "they", "them",
    "its", "it's", "per", "via", "than", "also", "more", "most", "any", "all", "one", "out",
}


def _period(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "").strip())
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else text + "."


def _content_tokens(text: str) -> set[str]:
    out = set(re.findall(r"\d[\d.,]*%?", text or ""))
    for word in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}", text or ""):
        low = word.lower().strip(".")
        if low in _FUNC:
            continue
        if word[0].isupper() or any(ch.isdigit() for ch in word) or len(low) >= 5:
            out.add(low)
    return out


def _grounded(statement: str, source: str) -> list[str]:
    src = (source or "").lower()
    missing = []
    for token in sorted(_content_tokens(statement)):
        bare = token[1:] if token.startswith("$") else token
        if token in src or bare in src or bare.rstrip("s") in src:
            continue
        missing.append(token)
    return missing


def resolve_cap(config: dict | None) -> tuple[int | None, str | None]:
    """Return (cap, reason). A missing ramp day is unknown, not day zero."""
    if not config:
        return None, "cap config not attached"
    ramp = config.get("ramp")
    if isinstance(ramp, dict) and ramp.get("steps"):
        steps = list(ramp.get("steps") or [])
        idx = ramp.get("day_index")
        if idx is None:
            return None, "ramp day index unknown; not assumed to be day 0"
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0 or not steps:
            return None, "ramp day index invalid"
        try:
            return int(steps[min(idx, len(steps) - 1)]), None
        except (TypeError, ValueError):
            return None, "ramp step invalid"
    if config.get("daily_cap") is None:
        return None, "daily cap not in config"
    try:
        return int(config["daily_cap"]), None
    except (TypeError, ValueError):
        return None, "daily cap invalid"


def _cap_state(config: dict | None, posted_today) -> dict:
    cap, reason = resolve_cap(config)
    if cap is None:
        return {"enforced": False, "allowed": None, "cap": None, "posted_today": posted_today
                if posted_today is not None else None, "reason": reason}
    if posted_today is None:
        return {"enforced": False, "allowed": None, "cap": cap, "posted_today": None,
                "reason": "posted_today unknown; not assumed 0"}
    try:
        posted = int(posted_today)
    except (TypeError, ValueError):
        return {"enforced": False, "allowed": None, "cap": cap, "posted_today": None,
                "reason": "posted_today invalid; not assumed 0"}
    return {"enforced": True, "allowed": posted < cap, "cap": cap, "posted_today": posted, "reason": None}


def draft_own_post(item: dict | None, *, seen: list[dict] | None = None, config: dict | None = None,
                   posted_today=None) -> dict:
    """Grounded English what-changed + why-it-matters. Not a quote-tweet and not a translation dump."""
    item = dict(item or {})
    source = str(item.get("source_text") or "")
    change = str(item.get("what_changed") or "").strip()
    why = str(item.get("why_it_matters") or "").strip()
    reasons = []
    if not source.strip():
        reasons.append("missing_primary_source")
    if not change or not why:
        reasons.append("missing_change_or_importance")
    if change and why and re.sub(r"\W+", "", change.lower()) == re.sub(r"\W+", "", why.lower()):
        reasons.append("why_repeats_change")
    if _QT_RE.search(change) or _QT_RE.search(why) or str(item.get("mode") or item.get("kind") or "") in {"qt", "translation", "quote"}:
        reasons.append("not_original")
    if _OPINION_RE.search(change) or _OPINION_RE.search(why):
        reasons.append("invented_opinion_or_personal_testing")
    if source and change:
        missing = _grounded(change, source)
        if missing:
            reasons.append("change_not_in_source")
    if source and why:
        missing = _grounded(why, source)
        if missing:
            reasons.append("why_not_grounded")
    urls = [u for u in (item.get("primary_urls") or []) if sanitize_source_url(u)]
    if item.get("primary_urls") and not urls:
        reasons.append("primary_url_not_public_https")
    news_item = {
        "source_text": source,
        "url": (urls or [None])[0],
        "extra_urls": urls,
        "author": item.get("author"),
        "author_role": item.get("author_role"),
        "aggregator": item.get("aggregator"),
        "classification": item.get("classification"),
        "kind": item.get("news_kind") or item.get("kind") if item.get("kind") in {"useful_case", "news", "product_update"} else None,
        "official": item.get("official"),
        "first_party": item.get("first_party"),
        "quoted_text": item.get("quoted_text"),
        "lane": "own_content",
    }
    verdict = assess_newsworthiness(news_item)
    if not verdict["allow"]:
        reasons.append(verdict["reason"] or "not_newsworthy")
    keys = story_keys({"source_text": source, "url": news_item.get("url"), "extra_urls": urls,
                       "headline": change, "claim": source})
    dup = duplicate_reason(keys, seen)
    if dup:
        reasons.append(dup)
    cap = _cap_state(config, item.get("posted_today", posted_today))
    text = None
    if change and why and "not_original" not in reasons:
        text = f"{_period(change)} {_period(why)}"
    text_reasons = [r for r in reasons if r != cap.get("reason")]
    ok = not text_reasons
    if config is not None and config.get("enabled") is False:
        reasons.append("lane_disabled")
        ok = False
        ready = False
    elif config is None or config.get("enabled") is None:
        ready = False
    else:
        ready = bool(ok and cap.get("allowed") is True)
    return {
        "ok": ok,
        "text": text if ok else None,
        "reasons": list(dict.fromkeys(reasons)),
        "story_keys": keys,
        "account": ACCOUNT,
        "language": "en",
        "x_api": "off",
        "ready_for_executor": ready,
        "cap": cap,
        "verdict": verdict,
        "handoff": None if not ok else {
            "executor": "chrome_extension",
            "invoked": False,
            "x_api": False,
            "cookies": False,
            "browser": False,
            "account": ACCOUNT,
            "text": text,
            "story_keys": keys,
            "primary_urls": urls,
        },
    }


def operational_status(config: dict | None = None, history: dict | None = None) -> dict:
    """Truthful lane status. Missing history is null, not zero."""
    cap, cap_reason = resolve_cap(config)
    posted = None
    if history is not None and history.get("posted_today") is not None:
        try:
            posted = int(history["posted_today"])
        except (TypeError, ValueError):
            posted = None
    enabled = None if not config or "enabled" not in config else bool(config.get("enabled"))
    missing = []
    if config is None:
        missing.append("live own-content config was not attached")
    elif cap is None and cap_reason:
        missing.append(cap_reason)
    if history is None or history.get("posted_today") is None:
        missing.append("posted_today was not attached and is not reported as 0")
    missing.append("live Chrome extension executor was not in the provided snapshots")
    return {
        "available": False,
        "account": ACCOUNT,
        "lane": "own_content_en",
        "language": "en",
        "x_api": "off",
        "executor": "chrome_extension",
        "executor_loaded": False,
        "enabled": enabled,
        "daily_cap": cap,
        "ramp": None if not config else config.get("ramp"),
        "posted_today": posted,
        "metrics": None,
        "unavailable_reason": "; ".join(dict.fromkeys(missing)),
    }
