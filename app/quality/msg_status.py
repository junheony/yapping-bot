"""MSG hub summary and /api/tg_status builders.

Unknown candidate, rejection, and queue history stays null. Partial rejection
rows are returned separately and are not a pass rate. Private Telegram targets
become the generic label, with no chat or message id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .tg_news import (
    enforcement_status,
    public_telegram_url,
    redact_channel,
    sanitize_source_url,
)
from .x_own import operational_status

KST = timezone(timedelta(hours=9))

POLICY = {
    "scope": "important_or_useful_news_and_practical_ai_cases",
    "fame": "not_sufficient",
    "aggregators": "discovery_only_verify_primary",
    "dedup": "one_story_across_accounts_and_images",
    "korean": "dry_da_headline_plus_distinct_why",
    "media": "official_capture_or_verified_demo_else_text_preview_off",
    "invention": "no_invented_ai_context_or_firsthand_use",
}

# Confirmed from the public source posts. fix_status stays prepared until the
# sender process attests that this code is what it is running.
KNOWN_QUALITY_ISSUES = [
    {
        "post_url": "https://t.me/cblaboratory/795",
        "source_url": "https://x.com/Muse/status/2118411960337289583",
        "findings": [
            "Source attachment is a video, not a photo.",
            "Sender would select the video thumbnail URL (amplify_video_thumb) after text was accepted.",
            "That frame is a racetrack paddock scene, unrelated to the Voice/cooking text.",
            "The selected media URL was not retained, so provenance was unknown.",
        ],
        "certainty": "confirmed",
        "fix_status": "prepared",
        "lane": None,
    },
    {
        "post_url": "https://t.me/cblaboratory/796",
        "source_url": "https://x.com/thsottiaux/status/2118938956301779171",
        "findings": [
            "Source text is the joke 'Get me out of the basement' plus a poteto.com game link.",
            "Quoted text is 'It's Friday'.",
            "No AI-made game and no OpenAI product update is in the source.",
            "openai:update/agent/top_tier treats author/employer fame as if it were news.",
        ],
        "certainty": "confirmed",
        "fix_status": "prepared",
        "lane": None,
    },
]


def _as_of(snapshot: dict) -> str | None:
    if snapshot.get("as_of"):
        return str(snapshot["as_of"])
    return None


def _issue_fix_status(enforcement: str) -> str:
    if enforcement == "live":
        return "live"
    if enforcement == "testing":
        return "testing"
    return "prepared"


def _quality_issues(enforcement: str) -> list[dict]:
    status = _issue_fix_status(enforcement)
    out = []
    for issue in KNOWN_QUALITY_ISSUES:
        post = public_telegram_url(issue["post_url"])
        source = sanitize_source_url(issue["source_url"])
        out.append({
            "post_url": post,
            "findings": list(issue["findings"]),
            "certainty": issue["certainty"],
            "fix_status": status,
            "lane": issue.get("lane"),
            "source_url": source,
        })
    return out


def _redact_recent(rows: list[dict] | None) -> list[dict] | None:
    if rows is None:
        return None
    out = []
    for row in rows:
        channel = redact_channel(row.get("channel"))
        tg = public_telegram_url(row.get("telegram_url"))
        if channel == "private group":
            tg = None
        item = {
            "at": row.get("at"),
            "channel": channel,
            "lane": row.get("lane"),
            "ok": row.get("ok"),
            "telegram_url": tg,
            "source_url": sanitize_source_url(row.get("source_url") or row.get("url")),
        }
        if row.get("headline"):
            headline = str(row["headline"])
            headline = headline.replace(str(row.get("telegram_url") or ""), "")
            item["headline"] = headline.strip()
        out.append(item)
    return out


def _channel_lane_rows(events: list[dict] | None, *, failures_known: bool) -> list[dict] | None:
    if events is None:
        return None
    grouped: dict[tuple[str, str], dict] = {}
    for ev in events:
        channel = redact_channel(ev.get("channel")) or ""
        lane = str(ev.get("lane") or "")
        slot = grouped.setdefault((channel, lane), {
            "channel": channel, "lane": lane, "attempts": 0, "success": 0,
            "failure": 0 if failures_known else None,
            "last_success_at": None, "last_failure_at": None,
        })
        slot["attempts"] += 1
        if ev.get("ok"):
            slot["success"] += 1
            slot["last_success_at"] = ev.get("at")
        elif failures_known:
            slot["failure"] += 1
            slot["last_failure_at"] = ev.get("at")
        else:
            slot["failure"] = None
    return list(grouped.values())


def _pass_rate(candidates, rejected) -> tuple[float | None, str | None]:
    if candidates is None or rejected is None:
        return None, "candidate or rejection history was not attached"
    try:
        cand = int(candidates)
        rej = int(rejected)
    except (TypeError, ValueError):
        return None, "candidate or rejection history was not numeric"
    if cand <= 0 or rej < 0 or rej > cand:
        return None, "candidate history cannot produce a rate"
    return (cand - rej) / cand, None


def build_telegram_section(snapshot: dict | None = None) -> dict:
    snap = dict(snapshot or {})
    enforcement = snap.get("policy_enforcement_status") or enforcement_status()
    if enforcement not in {"not_applied", "prepared", "testing", "live"}:
        enforcement = "not_applied"
    events = snap.get("sent_events")
    failures_known = bool(snap.get("failures_known"))
    sourcing_in = snap.get("sourcing") if isinstance(snap.get("sourcing"), dict) else None
    if sourcing_in and sourcing_in.get("candidates") is not None and sourcing_in.get("rejected") is not None:
        rate, rate_reason = _pass_rate(sourcing_in.get("candidates"), sourcing_in.get("rejected"))
        sourcing = {
            "candidates": sourcing_in.get("candidates"),
            "rejected": sourcing_in.get("rejected"),
            "pass_rate": rate,
            "unavailable_reason": rate_reason,
        }
    else:
        sourcing = {
            "candidates": None,
            "rejected": None,
            "pass_rate": None,
            "unavailable_reason": "candidate and rejection history was not in the attached snapshot",
        }
    if snap.get("rejection_log_complete") and isinstance(snap.get("rejections"), list):
        observed = snap.get("rejections")
        partial = None
    else:
        observed = None
        partial = snap.get("partial_rejections") if isinstance(snap.get("partial_rejections"), list) else None
    pending = snap.get("pending") if isinstance(snap.get("pending"), dict) else None
    if pending and pending.get("count") is not None:
        pending_out = {"count": pending.get("count"), "source": pending.get("source") or "attached_queue"}
    else:
        pending_out = {"count": None, "source": None}
    coverage = {
        "sent_log": "complete" if events is not None and failures_known else ("partial" if events is not None else "missing"),
        "candidate_history": "complete" if sourcing["pass_rate"] is not None else "missing",
        "rejection_history": "complete" if observed is not None else ("partial" if partial else "missing"),
        "notes": [],
    }
    if events is None:
        coverage["notes"].append("sent log was not attached; today counts are null, not zero")
    elif not failures_known:
        coverage["notes"].append("failure counts are null because the attached log does not include failures")
    if sourcing["pass_rate"] is None:
        coverage["notes"].append(sourcing["unavailable_reason"])
    if pending_out["count"] is None:
        coverage["notes"].append("pending queue was not attached")
    if partial:
        coverage["notes"].append("partial rejection rows are labeled separately and are not a pass rate")
    today_rows = _channel_lane_rows(events, failures_known=failures_known)
    return {
        "as_of": _as_of(snap),
        "timezone": "Asia/Seoul",
        "policy": POLICY,
        "policy_enforcement_status": enforcement,
        "today": {
            "by_channel_lane": today_rows,
            "unavailable_reason": None if today_rows is not None else "sent log was not attached",
        },
        "pending": pending_out,
        "sourcing": sourcing,
        "observed_rejections": observed,
        "partial_rejections": [
            {"reason": row.get("reason"), "count": row.get("count"), "coverage": "partial"}
            for row in (partial or [])
        ] if partial else None,
        "recent": _redact_recent(snap.get("recent") if "recent" in snap else events),
        "quality_issues": _quality_issues(enforcement),
        "coverage": coverage,
    }


def build_tg_status(snapshot: dict | None = None) -> dict:
    """Match the live shape: today_by_channel values are lane lists, not counts."""
    snap = dict(snapshot or {})
    events = snap.get("sent_events")
    today_by_channel = None
    today_by_lane = None
    if events is not None:
        today_by_channel = {}
        today_by_lane = {}
        for ev in events:
            if ev.get("ok") is False:
                continue
            channel = redact_channel(ev.get("channel")) or ""
            lane = str(ev.get("lane") or "")
            today_by_channel.setdefault(channel, []).append(lane)
            today_by_lane.setdefault(lane, []).append(channel)
    recent = _redact_recent(snap.get("recent")) if "recent" in snap else _redact_recent(events[-8:] if events else None)
    binance = snap.get("binance_ann", None)
    if isinstance(binance, list):
        cleaned = []
        for row in binance:
            if isinstance(row, str):
                cleaned.append(sanitize_source_url(row) or None)
            elif isinstance(row, dict):
                cleaned.append({k: (sanitize_source_url(v) if k.endswith("url") else v) for k, v in row.items()
                                if "token" not in k.lower() and "secret" not in k.lower()})
        binance_out = [x for x in cleaned if x]
    else:
        binance_out = None
    enabled = snap.get("enabled", None)
    if enabled is not None:
        enabled = bool(enabled)
    return {
        "enabled": enabled,
        "today_by_channel": today_by_channel,
        "today_by_lane": today_by_lane,
        "recent": recent,
        "binance_ann": binance_out,
        "binance_ann_unavailable_reason": None if binance_out is not None else "binance announcement source was not attached",
    }


def build_hub_summary(snapshot: dict | None = None) -> dict:
    snap = dict(snapshot or {})
    now = datetime.now(KST).isoformat(timespec="seconds")
    telegram_in = dict(snap.get("telegram") or {})
    if "as_of" not in telegram_in:
        telegram_in["as_of"] = snap.get("as_of") or now
    own = snap.get("own_content")
    if isinstance(own, dict) and ("config" in own or "history" in own):
        own_status = operational_status(own.get("config"), own.get("history"))
    else:
        own_status = operational_status(None, None)
    return {"telegram": build_telegram_section(telegram_in), "own_content": own_status}


def response_leaks_private(payload) -> list[str]:
    """Test helper: private ids and credential fragments that must not appear."""
    import json
    text = json.dumps(payload, ensure_ascii=False)
    hits = []
    for needle in ("t.me/c/", "joinchat", "token=", "password=", "access_token"):
        if needle in text:
            hits.append(needle)
    return hits
