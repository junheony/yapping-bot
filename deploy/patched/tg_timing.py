"""2026-11-02 tggrowth: Telegram posting windows (human-curated feel: posts land when people read the channel).

- non-breaking lanes (hot, report, geeknews, data, forward, weekly, non-urgent macro) post only inside `windows`
  (default 12:00-13:00 and 16:00-18:00 KST); outside a window they wait in kv tg_queue (max_hold_h, per lane hold_h).
- `quiet`: per-channel hours with no non-breaking post at all (@blockschool 18:00-22:00).
- breaking + urgent macro (FOMC/CPI/jobs/PCE/rate decisions) go immediately inside `urgent_window` (08:00-24:00);
  outside it they are queued as urgent and flushed from 08:00.
- flush() (scheduler tick, 300 s): inside a window, one item per channel per flush (urgent first, then oldest),
  re-running tg_curate + dedupe at flush time; expired items are dropped (logged).
targets.yaml tg_timing.enabled: false -> everything sends immediately (old behaviour)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from .. import accounts, config, db

log = logging.getLogger("yb.tgtiming")
KV_Q = "tg_queue"
KV_DROP = "tg_queue_dropped"
IMMEDIATE_LANES = ("breaking",)


def cfg() -> dict:
    c = dict(config.targets().get("tg_timing") or {})
    c.setdefault("enabled", False)
    c.setdefault("windows", [["12:00", "13:00"], ["16:00", "18:00"]])
    c.setdefault("quiet", accounts.get("telegram.quiet"))
    c.setdefault("urgent_window", ["08:00", "24:00"])
    c.setdefault("max_hold_h", 8)
    hh = {"data": 6, "breaking": 9, "macro_urgent": 9}
    hh.update(c.get("hold_h") or {})
    c["hold_h"] = hh
    c.setdefault("per_flush", 1)
    return c


def overnight_cfg() -> dict:
    """2026-11-03 tgovernight (owner: 새벽에도 중요한 소식은 올려야지): targets.yaml tg_overnight."""
    c = dict(config.targets().get("tg_overnight") or {})
    c.setdefault("enabled", False)
    c.setdefault("window", ["00:00", "08:00"])
    c.setdefault("min_importance", 4)
    c.setdefault("require_first_party", True)
    c.setdefault("cap_bonus", 2)
    return c


def overnight_ok(lane: str, now: datetime, importance: int = 0, first_party: bool = False) -> bool:
    """Important primary-source news may go to a public channel immediately during the overnight window."""
    o = overnight_cfg()
    if not o["enabled"] or lane in ("forward", "storyline", "weekly"):
        return False
    if not in_range(now, *o["window"]):
        return False
    if o["require_first_party"] and not first_party:
        return False
    return int(importance or 0) >= int(o["min_importance"])


def _mins(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def in_range(now: datetime, a: str, b: str) -> bool:
    t = now.hour * 60 + now.minute
    lo, hi = _mins(a), _mins(b)
    return lo <= t < hi if lo <= hi else (t >= lo or t < hi)


def in_window(now: datetime, c: dict | None = None) -> bool:
    c = c or cfg()
    return any(in_range(now, a, b) for a, b in c["windows"])


def next_window_end(now: datetime, c: dict | None = None) -> datetime:
    """End of the next posting window that has not ended yet (today or tomorrow)."""
    c = c or cfg()
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ends = []
    for d in (0, 1):
        for a, b in c["windows"]:
            e = base + timedelta(days=d, minutes=_mins(b))
            if e > now:
                ends.append(e)
    return min(ends) if ends else now + timedelta(hours=24)


def quiet(target: str, now: datetime, c: dict | None = None) -> bool:
    c = c or cfg()
    return any(in_range(now, a, b) for a, b in (c["quiet"].get(str(target)) or []))


def decide(lane: str, target: str, now: datetime | None = None, urgent: bool = False, importance: int = 0,
           first_party: bool = False) -> str:
    """'now' | 'queue'."""
    c = cfg()
    if not c["enabled"]:
        return "now"
    now = now or db.now_kst()
    try:   # 2026-11-02 newsfix: Dev. Group NEWS (any non-public target) is unchanged = immediate
        from . import tg_route
        if str(target) not in set(tg_route.public_channels()):
            return "now"
    except Exception:
        pass
    if lane in IMMEDIATE_LANES or urgent:
        if in_range(now, *c["urgent_window"]):
            return "now"
        # 2026-11-03 tgovernight: importance >= tg_overnight.min_importance primary-source news is not held overnight
        return "now" if overnight_ok(lane, now, importance, first_party) else "queue"
    if quiet(target, now, c):
        return "queue"
    return "now" if in_window(now, c) else "queue"


def queue() -> list[dict]:
    return db.kv_get(KV_Q, []) or []


def enqueue(item: dict, now: datetime | None = None, hold_h: float | None = None) -> dict | None:
    """Queue one (lane, target, body, ...) send. Same ref+target already queued -> no second copy."""
    c = cfg()
    now = now or db.now_kst()
    q = queue()
    ref, t = item.get("ref"), item.get("target")
    if ref and any(x.get("ref") == ref and x.get("target") == t for x in q):
        return None
    lane = item.get("lane", "")
    key = "macro_urgent" if lane == "macro" and item.get("urgent") else lane
    h = float(hold_h if hold_h is not None else c["hold_h"].get(key, c["max_hold_h"]))
    exp = max(now + timedelta(hours=h), next_window_end(now, c))   # 2026-11-02 newsfix: never expire before a window
    x = dict(item, qid=uuid.uuid4().hex[:10], queued_at=db.iso(now), expires_at=db.iso(exp))
    q.append(x)
    db.kv_set(KV_Q, q[-200:])
    log.info("tg queued [%s] %s -> %s until %s", lane, ref, t, x["expires_at"][11:16])
    return x


def _drop(x: dict, why: str, now: datetime) -> None:
    from . import tg_route
    lg = db.kv_get(KV_DROP, []) or []
    lg.append({"at": db.iso(now), "lane": x.get("lane"), "target": x.get("target"), "ref": x.get("ref"), "why": why})
    db.kv_set(KV_DROP, lg[-200:])
    log.warning("tg queue drop [%s] %s -> %s: %s", x.get("lane"), x.get("ref"), x.get("target"), why)
    tg_route.lane_log_set(x.get("kv_log"), x.get("ref"), x.get("target"), "err: dropped from queue: " + why[:80])
    log.info("tg queue drop [%s] %s -> %s: %s", x.get("lane"), x.get("ref"), x.get("target"), why)


def _already(x: dict) -> bool:
    from . import tg_route
    ref, t = x.get("ref"), x.get("target")
    if not ref:
        return False
    return any(e.get("ok") and e.get("ref") == ref and e.get("channel") == t for e in tg_route.sent_log())


def flush(now: datetime | None = None, dry_run: bool = False) -> dict:
    """Send due queue items: one per channel per flush. Returns {"sent": [...], "dropped": [...], "kept": n}."""
    from . import tg_curate, tg_route
    c = cfg()
    now = now or db.now_kst()
    q = queue()
    out = {"sent": [], "dropped": [], "kept": 0, "would_send": []}
    if not q:
        return out
    keep, done_t = [], {}
    q.sort(key=lambda x: (0 if x.get("urgent") or x.get("lane") in IMMEDIATE_LANES else 1, x.get("queued_at", "")))
    for x in q:
        t = x.get("target")
        if str(x.get("expires_at", "")) < db.iso(now):
            out["dropped"].append((x.get("ref"), t, "expired"))
            if not dry_run:
                _drop(x, "expired", now)
            continue
        if _already(x):
            out["dropped"].append((x.get("ref"), t, "already sent"))
            if not dry_run:
                _drop(x, "already sent", now)
            continue
        # 2026-11-07: legacy rows skipped text/media checks when they were queued. Re-check before send.
        from ..quality.tg_news import vet_queued
        others = [y for y in q if y.get("qid") != x.get("qid")]
        seen = [e for e in tg_route.sent_log() if e.get("ok")] + others
        vetted = vet_queued(x, seen)
        if vetted["drop"]:
            out["dropped"].append((x.get("ref"), t, vetted["reason"]))
            if not dry_run:
                _drop(x, "quality: " + str(vetted["reason"]), now)
            continue
        x = dict(x, body=vetted["body"], photo=vetted["photo"], media=vetted.get("media"), no_preview=True)
        if done_t.get(t, 0) >= int(c["per_flush"]) or (c["enabled"] and
                                                       decide(x.get("lane", ""), t, now, bool(x.get("urgent")),
                                                              int(x.get("importance") or 0),
                                                              bool(x.get("first_party"))) != "now"):
            keep.append(x)
            continue
        why = tg_curate.check(x.get("lane", ""), now, entity=x.get("entity", ""),
                              first_party=bool(x.get("first_party")), importance=int(x.get("importance") or 4),
                              channel=t)
        if why:
            if "min gap" in why:
                keep.append(x)
            else:
                out["dropped"].append((x.get("ref"), t, why))
                if not dry_run:
                    _drop(x, "curate: " + why, now)
            continue
        done_t[t] = done_t.get(t, 0) + 1
        if dry_run:
            out["would_send"].append((x.get("lane"), x.get("ref"), t))
            keep.append(x)
            continue
        meta = {k: x.get(k) for k in ("entity", "topic", "ref", "item_id", "url", "headline", "quote",
                                      "source_text", "quoted_text", "author", "classification", "media", "story_keys",
                                      "extra_urls", "lane")}
        meta["no_preview"] = True
        r = tg_route._deliver_one(x.get("lane", ""), t, x.get("body", ""), x.get("parse_mode") or "HTML",
                                  x.get("photo"), meta)
        tg_route.lane_log_set(x.get("kv_log"), x.get("ref"), t, tg_route._res_str(r))
        if x.get("lane") == "forward" and r.get("ok"):
            try:
                from . import tg_forward
                tg_forward.mark_sent(x.get("ref"), t, r.get("message_id"))
            except Exception as e:
                log.warning("forward mark failed: %s", e)
        out["sent"].append((x.get("lane"), x.get("ref"), t, bool(r.get("ok"))))
    out["kept"] = len(keep)
    if not dry_run:
        # items enqueued while we were sending survive
        cur = {y.get("qid") for y in q}
        db.kv_set(KV_Q, keep + [y for y in queue() if y.get("qid") not in cur])
    return out


def tick(now: datetime | None = None, dry_run: bool = False) -> dict:
    return flush(now, dry_run)


def status() -> dict:
    c = cfg()
    now = db.now_kst()
    return {"enabled": c["enabled"], "in_window": in_window(now, c), "windows": c["windows"],
            "queue": [(x.get("lane"), x.get("target"), x.get("ref"), x.get("expires_at", "")[11:16]) for x in queue()],
            "dropped": (db.kv_get(KV_DROP, []) or [])[-10:]}
