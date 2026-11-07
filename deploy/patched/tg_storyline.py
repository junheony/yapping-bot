"""2026-11-02 tggrowth storyline: "community storyline" posts (t.me/minchoisfuture style) - a short Korean curator
comment + 2-4 lines on how a CT person's post spread (who amplified / who replied, @handles credited) + the original
X link(s), photo = the original post's media (tg_post.fx_media) or the X link preview.

Detection uses OUR data only (no X API):
  collector inbox (inbox/x/**.jsonl posts + users), kv breaking_pool, kv rising_top, ext/tg-agg/out/latest.json
  -> local graph: reply edges (in_reply_to_id), quote edges (status URL in text), shout-outs (@mentions that are not
     the leading reply mentions) from NOTABLE authors (followers >= notable_followers, official / key people, founders)
  -> tg-agg: an X post shared by >= tg_agg_min_channels independent Korean TG channels counts as one interaction
  -> rising top-N (kv rising_top) is a flag
  -> best subjects enriched via api.fxtwitter.com (public GET; status + conversation): the subject's own post, notable
     repliers (follower floor, or a founder/official account whose bio names an @project from the thread), velocity
  -> qualify: >= min_interactions distinct notable interactors, OR (rising top-N or views/h >= fast_vph) + 1.
Primary-sourced: every line is grounded in fetched posts (handles must be in the evidence, numbers in the sources);
promo / airdrop / referral threads, price calls and rumors are skipped (regex + LLM flags).
Routing: tg_route.topic -> crypto @blockschool, agent/AI @cblaboratory (never devgroup); lane "storyline" is a
forward-kind lane (tg_curate forward_cap/total_cap, tg_timing windows); storyline + forwards share shared_daily_cap
per channel per day; one storyline per person per person_hours (kv tg_storyline_log) + tg_sent_log dedupe.
targets.yaml tg_storyline.enabled: false = off.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from .. import accounts, config, db

log = logging.getLogger("yb.tgstory")
KV_LOG = "tg_storyline_log"
KV_SEEN = "tg_storyline_seen"
FX = "https://api.fxtwitter.com"
SELF = set(accounts.get("telegram.self_handles"))
STOP = {"bot", "grok", "everyone", "here", "all", "admin", "team", "gmail", "x", "elonmusk", "perplexity", "aixbt_agent"}
STATUS_RE = re.compile(r"(?:x|twitter|fxtwitter|vxtwitter|fixupx)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)")
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_@])@([A-Za-z0-9_]{2,15})(?![A-Za-z0-9_])")   # "@dorong_x가" too
FOUNDER_RE = re.compile(r"\b(?:co-?founder|founder|ceo|cto|founding)\b|대표|창업자|공동창업", re.I)
PROMO_RE = re.compile(
    r"airdrop|referral|ref(?:erral)?\s*code|invite\s*code|giveaway|whitelist|\bwl\b|presale|pre-sale|\bmint\s+(?:now|live)|"
    r"use\s+my\s+(?:code|link)|sign\s*up\s+(?:with|using)|join\s+(?:now|here)|retweet\s*(?:&|and|\+)\s*follow|"
    r"\brt\s*&\s*follow|에어드[랍롭]|레퍼럴|초대\s*코드|추천인|화이트리스트|프리세일|이벤트\s*참여", re.I)
META_RE = re.compile(r"제공된|증거(?!금)|evidence|자료(?:에|상)|확인할 수 없|원문에|입력", re.I)
SHOUT_RE = re.compile(r"\b(?:questions?|answers?|answered|replied|thread|take|post|points?|pointed|explained?|wrote|"
                      r"argues?|says?|said|asked|great|best|good|solid|smart|insightful|shout|credit|kudos|h/t|"
                      r"by\s+@|from\s+@|via\s+@|according\s+to)\b|질문|답변|스레드|정리|언급|지적", re.I)
PRICE_RE = re.compile(r"\b(?:price\s*target|\bpt\b|100x|1000x|moon(?:ing)?|send\s+it|pump(?:ing)?|long(?:ed)?\s+\$|"
                      r"short(?:ed)?\s+\$|entry|target\s*\$?\d)|목표가|떡상|가즈아", re.I)

SYSTEM = """You write ONE short Korean "community storyline" post for a Korean Telegram channel, from evidence posts
only (each evidence item is a real X post we fetched: handle, followers, role, kind, text, url).
Story shape: a crypto-twitter / AI person posted something -> notable accounts quoted, shouted out or replied
(maybe the project's founder/official answered) -> why it matters for readers.
Return JSON {"promo": bool, "price_call": bool, "rumor": bool, "worth": bool, "topic": "agent|crypto|macro|other",
 "comment": "...", "lines": ["...", "..."], "why": "..."}
- promo: true if the subject or the thread is an airdrop / referral / giveaway / whitelist / paid promo / shill.
- price_call: true if the core is a price prediction or trade call. rumor: true if the core claim is unverified gossip.
- worth: true only if the exchange is substantive (questions/answers, analysis, a debate, a correction) and readers of
  our channel would learn something. Pure hype, memes, thank-you chains: false.
- 2026-11-03 tgstyle3 (owner: "너무 AI 티 남, 전부 고쳐"): write it like a person sharing a thread with friends in
  their own channel, NOT a news summary of who said what. ONE post = ONE point.
- comment: ONE short casual Korean line (max 40 chars) = what the thread is really about, in your own words, e.g.
  "에이전트 붙일 때 제일 귀찮은 건 아직 MCP 설정인 듯". No emoji, no hype, no @handle.
- lines: 1-2 short casual Korean lines (max 80 chars each) = the single most interesting point of the exchange, in your
  own words (the problem / insight / correction, and whose thread it is). Name at most 2 people, by handle WITHOUT
  "@" (e.g. "stevelacey 스레드", "levelsio도 거들었음"), spelled exactly as in the evidence. Never list people one by
  one, never "A는 ~했다. B는 ~했다. C는 ~했다". The subject must be named once.
  Use ONLY facts in the evidence texts; no numbers that are not in the evidence; no investment advice; never invent
  what someone said; no URLs.
- Voice: dry Korean. One line says what it is, the next says why it matters and ends with ~다.
  Never ~임, ~함, ~네, ~요, or ~했음. BANNED summary-report verbs:
  짚었다, 설명했다, 지적했다, 전망했다, 강조했다, 밝혔다, 언급했다, 덧붙였다, 소개했다, 주장했다, 평가했다, 분석했다,
  내다봤다. Never self-talk endings (~네/~군/~구나/~느낌/~요) or vague lines ("~하는 방향", "새로운 흐름"),
  no translationese ("~를 통해", "~에 따르면", "또한", "한편"). Never mention the evidence/input itself ("제공된",
  "확인할 수 없다"): just leave out what is not there."""


def cfg() -> dict:
    c = dict(config.targets().get("tg_storyline") or {})
    c.setdefault("enabled", False)
    c.setdefault("window_h", 48)
    c.setdefault("notable_followers", 15000)
    c.setdefault("big_followers", 50000)
    c.setdefault("max_subject_followers", 300000)
    c.setdefault("min_interactions", 2)
    c.setdefault("rising_top_n", 20)
    c.setdefault("fast_vph", 3000)
    c.setdefault("tg_agg_min_channels", 2)
    c.setdefault("max_fx_subjects", 6)
    c.setdefault("max_judge", 3)
    c.setdefault("person_hours", 72)
    c.setdefault("shared_daily_cap", 1)
    c.setdefault("min_age_h", 2)
    c.setdefault("use_llm", True)
    c.setdefault("photo", True)
    return c


def enabled() -> bool:
    return bool(cfg()["enabled"])


# ------------------------------------------------------------------ local data
def _int(v) -> int:
    try:
        return int(float(str(v).replace(",", "")))
    except Exception:
        return 0


def _bool(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _norm(p: dict) -> dict | None:
    pid = str(p.get("id") or "")
    au = str(p.get("author_username") or p.get("author") or "").lower().lstrip("@")
    if not pid.isdigit() or not au:
        return None
    return {"id": pid, "author": au, "followers": _int(p.get("author_followers") or p.get("followers")),
            "text": str(p.get("text") or ""), "at": _ts(p.get("created_at")),
            "views": _int(p.get("impression_count") or p.get("views")),
            "reply_to": str(p.get("in_reply_to_id") or "") if str(p.get("in_reply_to_id") or "") not in ("", "None") else "",
            "repost": _bool(p.get("is_repost")), "url": p.get("url") or f"https://x.com/{au}/status/{pid}"}


def load(now: datetime, c: dict) -> tuple[dict, dict]:
    """posts {id: post} (window_h + reply targets) and users {handle: {followers, bio}} from inbox + breaking_pool."""
    cut = now.astimezone(timezone.utc) - timedelta(hours=float(c["window_h"]))
    fcut = cut.timestamp() - 6 * 3600
    posts, users = {}, {}
    root = str(config.INBOX_DIR)
    files = [f for pat in ("*.jsonl", "done/*.jsonl", "ai/*.jsonl", "ai/done/*.jsonl")
             for f in glob.glob(os.path.join(root, pat))]
    for f in files:
        try:
            if os.path.getmtime(f) < fcut:
                continue
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kind") == "user":
                    u = str(r.get("username") or "").lower()
                    if u:
                        users[u] = {"followers": _int(r.get("followers")), "bio": str(r.get("description") or "")}
                elif r.get("kind") in ("post", None):
                    p = _norm(r)
                    if p and p["at"] and p["at"] >= cut - timedelta(hours=24):
                        posts[p["id"]] = p
    for r in db.kv_get("breaking_pool", []) or []:
        p = _norm(r) if isinstance(r, dict) else None
        if p and p["at"] and p["at"] >= cut - timedelta(hours=24) and p["id"] not in posts:
            posts[p["id"]] = p
    for p in posts.values():
        u = users.setdefault(p["author"], {"followers": 0, "bio": ""})
        u["followers"] = max(u["followers"], p["followers"])
    return posts, users


def _key_people() -> set[str]:
    from . import breaking
    try:
        breaking.cfg()
    except Exception:
        pass
    return set(breaking.KEY_PEOPLE)


def _official(h: str) -> bool:
    from . import sourcing
    try:
        return sourcing.is_official_handle(h)
    except Exception:
        return False


def _stem(h: str) -> str:
    """variational_io -> variational, lighter_xyz -> lighter, perpdex_hq -> perpdex."""
    return re.sub(r"(?:_?(?:io|xyz|hq|labs?|fi|app|official|protocol|network|finance|exchange|dex|ai))+$", "", h.lower()).strip("_")


def role_of(h: str, followers: int, bio: str, context: str, c: dict, kp: set[str]) -> str | None:
    """Why this account is notable (None = not notable). Founder/official only when the bio's @project is in the
    thread (context) - a small founder answering about their own project counts."""
    h = h.lower()
    if h in SELF or h in STOP:
        return None
    if FOUNDER_RE.search(bio or ""):
        projs = {m.lower() for m in MENTION_RE.findall(bio or "")}
        hit = [p for p in projs if p in context or (len(_stem(p)) >= 4 and re.search(r"\b" + re.escape(_stem(p)), context))]
        if hit:
            return f"founder @{hit[0]}"
    if _official(h):
        return "official"
    if h in kp:
        return "key person"
    if followers >= int(c["big_followers"]):
        return "big"
    if followers >= int(c["notable_followers"]):
        return "notable"
    return None


def _shoutouts(text: str) -> list[str]:
    """@mentions that are not the leading reply mentions."""
    body = re.sub(r"^(?:\s*@[A-Za-z0-9_]{1,15})+\s*", "", text or "")
    return [m.lower() for m in MENTION_RE.findall(body)]


def graph(posts: dict, users: dict, now: datetime, c: dict) -> dict:
    """subject handle -> {"inter": {handle: [edge]}, "sids": Counter, "rising": rank|None, "tg": {...}|None}."""
    kp = _key_people()
    cut = now.astimezone(timezone.utc) - timedelta(hours=float(c["window_h"]))
    S = {}

    def add(subj, sid, a, kind, p, role):
        subj = subj.lower()
        if subj == a or subj in SELF:
            return
        s = S.setdefault(subj, {"inter": {}, "sids": Counter(), "rising": None, "tg": None})
        if sid:
            s["sids"][sid] += 1
        s["inter"].setdefault(a, []).append({"kind": kind, "role": role, "followers": users.get(a, {}).get("followers", 0),
                                             "text": p["text"], "url": p["url"], "id": p["id"], "views": p["views"]})

    for p in posts.values():
        if p["repost"] or not p["at"] or p["at"] < cut:
            continue
        a = p["author"]
        u = users.get(a) or {}
        ctx = p["text"].lower()
        tgt = posts.get(p["reply_to"]) if p["reply_to"] else None
        if tgt:
            ctx += " " + tgt["text"].lower() + " " + tgt["author"]
        role = role_of(a, u.get("followers", 0), u.get("bio", ""), ctx, c, kp)
        if not role:
            continue
        if tgt and tgt["author"] != a:
            add(tgt["author"], tgt["id"], a, "reply", p, role)
        quoted = set()
        for qa, qid in STATUS_RE.findall(p["text"]):
            if qa.lower() != a and qa.lower() not in quoted:
                quoted.add(qa.lower())
                add(qa, qid, a, "quote", p, role)
        for m in set(_shoutouts(p["text"])) - quoted - ({tgt["author"]} if tgt else set()):
            add(m, None, a, "mention", p, role)
    rt = db.kv_get("rising_top", {}) or {}
    for it in (rt.get("items") or [])[: int(c["rising_top_n"])]:
        au = str(it.get("author") or "").lower()
        if au and au not in SELF:
            s = S.setdefault(au, {"inter": {}, "sids": Counter(), "rising": None, "tg": None})
            s["rising"] = {"rank": it.get("rank"), "id": str(it.get("id")), "vph": it.get("velocity_vph")}
            s["sids"][str(it.get("id"))] += 1
    try:
        agg = json.load(open(os.path.join(config.ROOT, "ext", "tg-agg", "out", "latest.json"), encoding="utf-8"))
    except Exception:
        agg = {}
    for cd in agg.get("candidates") or []:
        n = _int(cd.get("n_independent_sources") or cd.get("n_channels"))
        au = str(cd.get("x_author") or "").lower()
        if n >= int(c["tg_agg_min_channels"]) and au and au not in SELF:
            s = S.setdefault(au, {"inter": {}, "sids": Counter(), "rising": None, "tg": None})
            s["tg"] = {"n": n, "id": str(cd.get("x_status_id")), "views": _int(cd.get("tg_views_total")),
                       "channels": [x.get("channel") for x in (cd.get("shares") or [])][:5]}
            s["sids"][str(cd.get("x_status_id"))] += 1
    return S


def _relay(e: dict) -> bool:
    from . import sourcing
    try:
        return sourcing.is_relay({"author_username": e["url"].split("/")[3], "text": e["text"]})
    except Exception:
        return False


def farm(s: dict) -> bool:
    """Engagement-farm / yap campaign: many accounts only @mentioning the subject."""
    mo = [a for a, es in s["inter"].items() if all(e["kind"] == "mention" for e in es)]
    return len(mo) >= 6 and len(mo) >= 0.8 * len(s["inter"])


def local_score(s: dict) -> float:
    """reply/quote 1.0, shout-out 0.7 (+0.3 if it credits 2-4 people), relay/news mention 0.3, bare mention 0.25,
    founder/official/key person +0.5, low-reach (views < 2% of followers) x0.3, rising +1, tg-agg +1."""
    w = 0.0
    for es in s["inter"].values():
        e = max(es, key=lambda e: e["kind"] != "mention")
        if e["kind"] != "mention":
            v = 1.0
        elif _relay(e) or len(set(_shoutouts(e["text"]))) > 4:
            v = 0.3             # news relay, or a thank-you list / tag chain
        elif SHOUT_RE.search(e["text"]):
            v = 0.7 + (0.3 if 2 <= len(set(_shoutouts(e["text"]))) <= 4 else 0.0)
        else:
            v = 0.25
        if str(e["role"]).startswith(("founder", "official", "key")):
            v += 0.5
        elif e.get("views") and e["views"] < 0.02 * max(1, e["followers"]):
            v *= 0.3            # follower count far above the reach of the post: farm-like account
        w += v
    return round(w + (1 if s["rising"] else 0) + (1 if s["tg"] else 0), 2)


# ------------------------------------------------------------------ fxtwitter enrichment (GET only)
def _fx(path: str) -> dict:
    from . import quality
    return quality._get_json(FX + path, timeout=10)


def _fx_status(user: str, sid: str) -> dict:
    return (_fx(f"/{user}/status/{sid}") or {}).get("tweet") or {}


def _fx_conv(sid: str) -> dict:
    try:
        return _fx(f"/2/conversation/{sid}") or {}
    except Exception:
        return {}


def _fx_post(t: dict) -> dict:
    a = t.get("author") or {}
    at = datetime.fromtimestamp(int(t["created_timestamp"]), timezone.utc) if t.get("created_timestamp") else None
    return {"id": str(t.get("id") or ""), "author": str(a.get("screen_name") or "").lower(),
            "handle": a.get("screen_name") or "", "followers": _int(a.get("followers")),
            "bio": str(a.get("description") or ""), "text": str(t.get("text") or ""), "at": at,
            "views": _int(t.get("views")), "likes": _int(t.get("likes")), "replies": _int(t.get("replies")),
            "url": t.get("url") or "", "raw": t}


def enrich(subj: str, s: dict, posts: dict, now: datetime, c: dict) -> dict:
    """Fetch the subject post + conversation; returns the evidence dict (or {"error": ...})."""
    kp = _key_people()
    sid = None
    for k, _ in s["sids"].most_common():
        lp = posts.get(k)
        if lp is None or lp["author"] == subj:
            sid = k
            break
    if not sid:     # mention-only subject: the shout-out post may quote the subject's post
        for es in s["inter"].values():
            for e in es:
                try:
                    q = (_fx_status(e["url"].split("/")[3], e["id"]).get("quote") or {})
                except Exception:
                    q = {}
                if str((q.get("author") or {}).get("screen_name") or "").lower() == subj:
                    sid = str(q.get("id"))
                    break
            if sid:
                break
    if not sid:
        return {"error": "no subject post (mentions only)"}
    try:
        sp = _fx_post(_fx_status(subj, sid))
    except Exception as e:
        return {"error": f"fx status {type(e).__name__}"}
    if not sp["id"] or sp["author"] != subj:
        return {"error": f"fx: status {sid} is not @{subj}'s post"}
    conv = _fx_conv(sid)
    reps = [_fx_post(r) for r in (conv.get("replies") or [])]
    qt = str((sp["raw"].get("quote") or {}).get("text") or "")
    base = " ".join([sp["text"], qt, subj] + [es[0]["text"] for es in s["inter"].values()]).lower()
    inter = {}
    for a, es in s["inter"].items():
        e = dict(es[0])
        if e["kind"] == "mention" and not any(x["kind"] != "mention" for x in es):
            e["kind"] = "shout-out"
        if e.get("id") in {x["id"] for x in reps}:
            e["kind"] = "reply"
        inter[a] = e
    for r in reps:
        if r["author"] in (subj, *inter.keys()):
            continue
        ctx = base + " " + " ".join(o["text"].lower() for o in reps if o["author"] != r["author"])   # not their own words
        role = role_of(r["author"], r["followers"], r["bio"], ctx, c, kp)
        if role:
            inter[r["author"]] = {"kind": "reply", "role": role, "followers": r["followers"], "text": r["text"],
                                  "url": r["url"] or f"https://x.com/{r['handle']}/status/{r['id']}", "id": r["id"],
                                  "views": r["views"], "handle": r["handle"]}
    age_h = max(0.1, (now.astimezone(timezone.utc) - sp["at"]).total_seconds() / 3600) if sp["at"] else None
    vph = round(sp["views"] / age_h, 1) if age_h else 0.0
    return {"subject": subj, "post": sp, "inter": inter, "rising": s["rising"], "tg": s["tg"], "age_h": age_h,
            "vph": vph, "n_replies_fetched": len(reps)}


def qualify(ev: dict, c: dict) -> tuple[bool, str]:
    n = len(ev["inter"]) + (1 if ev.get("tg") else 0)
    boost = bool(ev.get("rising")) or ev.get("vph", 0) >= float(c["fast_vph"])
    sp = ev["post"]
    if sp["followers"] > int(c["max_subject_followers"]):
        return False, f"subject too big ({sp['followers']} followers > {c['max_subject_followers']})"
    if _official(ev["subject"]):
        return False, "subject is an official/brand account"
    if role_of(ev["subject"], 0, sp["bio"], sp["text"].lower(), c, set()) and FOUNDER_RE.search(sp["bio"]):
        return False, "subject is the project's own founder (the reply side of a storyline)"
    if ev.get("age_h") is not None and ev["age_h"] < float(c["min_age_h"]):
        return False, f"too fresh ({ev['age_h']:.1f}h < {c['min_age_h']}h)"
    if ev.get("age_h") is not None and ev["age_h"] > float(c["window_h"]) + 24:
        return False, f"too old ({ev['age_h']:.0f}h)"
    for t in [sp["text"]] + [e["text"] for e in ev["inter"].values()]:
        if PROMO_RE.search(t) or _ad(t):
            return False, "promo/airdrop/referral thread"
    if PRICE_RE.search(sp["text"]):
        return False, "price call"
    if n >= int(c["min_interactions"]):
        return True, f"{n} notable interactions"
    if boost and n >= 1:
        return True, f"{'rising top' if ev.get('rising') else 'fast velocity'} + {n} notable interaction"
    return False, f"only {n} notable interaction(s){' (+rising)' if boost else ''}"


def _ad(t: str) -> bool:
    from . import tg_forward
    m = tg_forward.AD_RE.search(t or "")
    return bool(m) and m.group(0).lower() not in ("가입",)


# ------------------------------------------------------------------ write + validate + render
def evidence(ev: dict) -> list[dict]:
    sp = ev["post"]
    out = [{"handle": "@" + (sp["handle"] or ev["subject"]), "followers": sp["followers"], "role": "subject",
            "kind": "original post", "text": sp["text"][:1200], "url": sp["url"]}]
    if (sp["raw"].get("quote") or {}).get("text"):
        q = sp["raw"]["quote"]
        out.append({"handle": "@" + str((q.get("author") or {}).get("screen_name") or ""), "followers":
                    _int((q.get("author") or {}).get("followers")), "role": "quoted by the subject", "kind": "context",
                    "text": str(q.get("text") or "")[:600], "url": q.get("url") or ""})
    for a, e in sorted(ev["inter"].items(), key=lambda kv: -kv[1]["followers"])[:6]:
        out.append({"handle": "@" + (e.get("handle") or a), "followers": e["followers"], "role": e["role"],
                    "kind": e["kind"], "text": e["text"][:600], "url": e["url"]})
    if ev.get("tg"):
        out.append({"handle": "", "followers": 0, "role": "korean telegram", "kind": "shared",
                    "text": f"shared by {ev['tg']['n']} Korean Telegram channels", "url": ""})
    return out


def write(ev: dict, target: str, fix: dict | None = None) -> dict:
    from . import llm
    # Retain the profile's legacy substring selector (including custom target behavior).
    our = (f"{accounts.tg_channels()['agent']} (AI agents / AI dev tools)"
           if accounts.get("telegram.agent_channel_selector") in target
           else f"{accounts.tg_channels()['crypto']} (crypto + macro)")
    pl = {"our_channel": our, "evidence": evidence(ev)}
    if fix:   # 2026-11-03 tgstyle3: one rewrite round with the validator's reasons
        pl.update(fix)
    return llm.chat_json("tg_storyline", SYSTEM, json.dumps(pl, ensure_ascii=False), max_tokens=700,
                         temperature=0.5 if fix else 0.4)


def validate(j: dict, ev: dict) -> list[str]:
    """2026-11-03 tgstyle3: 1-2 casual lines, people named WITHOUT "@" (an "@" is stripped), subject named once,
    no summary-report verbs / self-talk / translationese, every lowercase Latin token (handle-like) grounded."""
    from . import data_reports, tg_post
    issues = []
    lines = [tg_post._clean(x) for x in (j.get("lines") or []) if str(x).strip()]
    if not 1 <= len(lines) <= 2:
        issues.append(f"{len(lines)} lines (need 1-2)")
    evd = evidence(ev)
    known = {e["handle"].lower() for e in evd if e["handle"]}
    src = "\n".join(e["text"] for e in evd)
    known |= {"@" + h.lower() for h in MENTION_RE.findall(src)}     # named in a fetched post
    bare = {k.lstrip("@") for k in known if k}
    src_l = src.lower()
    cm = tg_post._clean(j.get("comment") or "")
    out_lines = []
    for ln in lines:
        if len(ln) > 90:
            issues.append(f"line too long ({len(ln)})")
        for h in MENTION_RE.findall(ln):
            if "@" + h.lower() not in known:
                issues.append(f"@{h} not in evidence")
        ln = MENTION_RE.sub(lambda m: m.group(1), ln).replace("@", "")
        for tok in re.findall(r"(?<![A-Za-z0-9_])[a-z0-9_]*[a-z][a-z0-9_]*(?![A-Za-z0-9_])", ln):
            if len(tok) >= 3 and tok not in bare and tok not in src_l:
                issues.append(f"name not in evidence: {tok}")
        if META_RE.search(ln):
            issues.append(f"meta wording: {ln[:30]}")
        if tg_post.HYPE_RE.search(ln) or tg_post.ADVICE_RE.search(ln):
            issues.append(f"hype/advice: {ln[:30]}")
        ti = tg_post.tone_issue(ln)
        if ti:
            issues.append("line " + ti.replace("comment ", ""))
        bad = data_reports.ungrounded(MENTION_RE.sub("", ln), MENTION_RE.sub("", src))
        if bad:
            issues.append(f"ungrounded numbers {bad}")
        out_lines.append(ln)
    if cm and tg_post.REPORT_RE.search(cm):
        issues.append("comment summary-report verb")
    text_l = " ".join(out_lines).lower()
    named = {h for h in bare if h and re.search(r"(?<![a-z0-9_])" + re.escape(h) + r"(?![a-z0-9_])", text_l)}
    if ev["subject"].lower() not in named:
        issues.append("subject not named")
    if len(named) > 2:
        issues.append(f"too many people named ({len(named)})")
    j["lines"] = out_lines
    return issues


def _link_handles(s: str) -> str:
    from . import tg_post
    return MENTION_RE.sub(lambda m: f'<a href="https://x.com/{m.group(1)}">@{m.group(1)}</a>', tg_post.esc(s))


def links(ev: dict) -> list[str]:
    out = [ev["post"]["url"]]
    for e in sorted(ev["inter"].values(), key=lambda e: (not str(e["role"]).startswith("founder"), -e["followers"])):
        if e["kind"] == "reply" and str(e["role"]).startswith(("founder", "official")) and e["url"] not in out:
            out.append(e["url"])
            break
    return out


def render(comment: str, lines: list[str], ev: dict) -> str:
    """2026-11-03 tgstyle3: comment + 1-2 lines (plain text, no @links) + one "원문" link line (no raw URLs)."""
    from . import tg_post
    if tg_post.style().get("layout") != "plain":
        out = [tg_post.esc(comment)] if comment else []
        out.append("\n".join(_link_handles(x) for x in lines))
        out.append("\n".join(tg_post.esc(u) for u in links(ev)))
        return "\n\n".join(out)
    txt = "\n".join(tg_post.esc(re.sub(r"(?<![.])[.]$", "", x.strip()))   # TG voice: no final period
                    for x in ([comment] if comment else []) + list(lines))
    return f'{txt}\n\n<a href="{tg_post.html.escape(ev["post"]["url"], quote=True)}">원문</a>'



def media_for(ev: dict) -> dict:
    """Subject post only. Quoted-post images are not a fallback."""
    from . import tg_post
    sp = ev.get("post") or {}
    raw = sp.get("raw") if isinstance(sp.get("raw"), dict) else None
    try:
        return tg_post.decide_media(sp.get("url") or "", tweet=raw, story_text=sp.get("text") or "",
                                    fetch=raw is None)
    except Exception:
        return {"url": None, "provenance": None, "approved": False, "preview": False, "rejected": [],
                "reason": "media_decision_error"}


def photo_for(ev: dict, body: str, c: dict) -> str | None:
    from . import tg_post
    if not c["photo"] or tg_post.plain_len(body) > 1000:
        return None
    decision = media_for(ev)
    return decision.get("url") if decision.get("approved") else None


def target_for(tp: str | None) -> str | None:
    from . import tg_forward, tg_route
    a = tg_forward.target_alias(tp or "")
    return tg_route.alias(a) if a else None


def dup(ev: dict, target: str, now: datetime, c: dict) -> str | None:
    from . import tg_route
    from ..quality.tg_news import story_keys, duplicate_reason
    subj, sid = ev["subject"], ev["post"]["id"]
    quote = (ev["post"].get("raw") or {}).get("quote") if isinstance(ev["post"].get("raw"), dict) else {}
    keys = story_keys({"url": ev["post"].get("url"), "source_text": ev["post"].get("text"),
                       "quoted_text": (quote or {}).get("text"), "extra_urls": [(quote or {}).get("url")]})
    same = duplicate_reason(keys, [e for e in tg_route.sent_log() if e.get("ok")])
    if same:
        return same
    pcut = db.iso(now - timedelta(hours=float(c["person_hours"])))
    for x in db.kv_get(KV_LOG, []) or []:
        if sid and sid in str(x.get("ref", "")):
            return "storyline already posted for this post"
        live = str(x.get("at", "")) >= pcut and any(not str(v).startswith("err") for v in (x.get("sent") or {}).values())
        if live and x.get("subject") == subj:
            return f"storyline on @{subj} within {c['person_hours']}h"
        if live and (subj in (x.get("interactions") or []) or x.get("subject") in ev["inter"]):
            return f"same storyline as @{x.get('subject')} ({c['person_hours']}h)"
    cut = db.iso(now - timedelta(hours=48))
    for x in tg_route.sent_log():
        if not x.get("ok") or str(x.get("at", "")) < cut:
            continue
        if sid and sid in str(x.get("url") or "") + str(x.get("ref") or ""):
            return "this X post is already on TG"
        if x.get("channel") == target and str(x.get("entity") or "") == subj:
            return f"@{subj} already on {target} in 48h"
    for r in db.q("SELECT id FROM ai_news_items WHERE created_at >= ? AND tg_posted_at IS NOT NULL AND "
                  "(link LIKE ? OR quote_url LIKE ?)", (cut, f"%/status/{sid}%", f"%/status/{sid}%")) if sid else []:
        return f"X post already posted (item {r['id']})"
    return None


def shared_today(target: str, now: datetime) -> int:
    """Storylines + forwards sent or queued to target today (KST)."""
    from . import tg_forward
    d = db.kst_day(now)
    n = 0
    for kv in (KV_LOG, tg_forward.KV_LOG):
        n += len([x for x in db.kv_get(kv, []) or [] if str(x.get("at", ""))[:10] == d and
                  any(k == target and not str(v).startswith("err") for k, v in (x.get("sent") or {}).items())])
    return n


# ------------------------------------------------------------------ tick
def candidates(now: datetime, c: dict) -> dict:
    posts, users = load(now, c)
    S = graph(posts, users, now, c)
    ranked = sorted(((h, s) for h, s in S.items() if s["inter"] or s["tg"]), key=lambda kv: -local_score(kv[1]))
    pre, farms = [], []
    for h, s in ranked:
        if h in STOP or _official(h):
            continue
        if farm(s):
            farms.append(h)
            continue
        if local_score(s) >= 0.7:
            pre.append((h, s))
    return {"posts": posts, "users": users, "graph": S, "pre": pre, "farms": farms, "n_posts": len(posts)}


def tick(now: datetime | None = None, dry_run: bool = False, top: int = 3, use_llm: bool | None = None,
         only: list[str] | None = None, max_fx: int | None = None, max_judge: int | None = None) -> dict:
    from . import tg_curate, tg_post, tg_route
    c = cfg()
    now = now or db.now_kst()
    if not c["enabled"] and not dry_run:
        return {"enabled": False}
    use_llm = c["use_llm"] if use_llm is None else use_llm
    seen = db.kv_get(KV_SEEN, {}) or {}
    cd = candidates(now, c)
    out = {"n_posts": cd["n_posts"], "n_subjects": len(cd["graph"]), "n_pre": len(cd["pre"]),
           "farms": cd["farms"][:12], "checked": [],
           "picks": [], "posted": {}}
    fx_left, llm_left = int(max_fx or c["max_fx_subjects"]), int(max_judge or c["max_judge"])
    pre = [x for x in cd["pre"] if not only or x[0] in only]
    for subj, s in pre:
        if fx_left <= 0:
            break
        key = f"{subj}:{s['sids'].most_common(1)[0][0] if s['sids'] else ''}"
        sn = seen.get(key) or {}
        if not dry_run and sn and ("worth" in sn or str(sn.get("at", "")) >= db.iso(now - timedelta(hours=6))):
            continue
        if any(subj in p_["interactions"] or p_["subject"] in s["inter"] for p_ in out["picks"]):
            out["checked"].append({"subject": subj, "ok": False, "reason": "same storyline as an earlier pick"})
            continue
        fx_left -= 1
        ev = enrich(subj, s, cd["posts"], now, c)
        info = {"subject": subj, "local": {a: (es[0]["kind"], es[0]["role"], es[0]["followers"])
                                           for a, es in s["inter"].items()},
                "rising": s["rising"], "tg": s["tg"]}
        if ev.get("error"):
            out["checked"].append(dict(info, ok=False, reason=ev["error"]))
            continue
        info.update({"url": ev["post"]["url"], "followers": ev["post"]["followers"], "views": ev["post"]["views"],
                     "age_h": round(ev["age_h"] or 0, 1), "vph": ev["vph"],
                     "interactions": {a: (e["kind"], e["role"], e["followers"]) for a, e in ev["inter"].items()}})
        ok, why = qualify(ev, c)
        if not ok:
            out["checked"].append(dict(info, ok=False, reason=why))
            if not dry_run:
                seen[key] = {"at": db.iso(now), "why": why}
            continue
        tp, _ = tg_route.topic_det(ev["post"]["text"] + "\n" + "\n".join(e["text"] for e in ev["inter"].values()))
        target = target_for(tp) or target_for("crypto")
        j = {}
        if use_llm:
            if llm_left <= 0:
                out["checked"].append(dict(info, ok=None, reason="llm budget"))
                continue
            llm_left -= 1
            try:
                j = write(ev, target)
            except Exception as e:
                out["checked"].append(dict(info, ok=False, reason=f"llm {type(e).__name__}"))
                continue
            if not dry_run:
                seen[key] = {"at": db.iso(now), "worth": j.get("worth")}
            if j.get("promo") or j.get("price_call") or j.get("rumor") or not j.get("worth"):
                out["checked"].append(dict(info, ok=False, reason=f"llm: promo={j.get('promo')} price={j.get('price_call')} "
                                           f"rumor={j.get('rumor')} worth={j.get('worth')} {str(j.get('why'))[:80]}"))
                continue
            if j.get("topic") in ("agent", "crypto", "macro"):
                tp = j["topic"]
                target = target_for(tp)
            elif j.get("topic") == "other":
                out["checked"].append(dict(info, ok=False, reason="llm topic other"))
                continue
        issues = validate(j, ev) if use_llm else ["no llm (lines not written)"]
        if issues and use_llm and not any(x.startswith(("@", "ungrounded", "hype")) for x in issues):
            try:   # 2026-11-03 tgstyle3: one rewrite round for style issues (same guards apply)
                j2 = write(ev, target, fix={"rejected_previous": {"comment": j.get("comment"), "lines": j.get("lines")},
                                            "rejected_because": issues[:6]})
                if not (j2.get("promo") or j2.get("price_call") or j2.get("rumor")) and j2.get("worth"):
                    i2 = validate(j2, ev)
                    if not i2:
                        j, issues = j2, []
            except Exception as e:
                log.info("storyline rewrite failed: %s", type(e).__name__)
        if issues:
            out["checked"].append(dict(info, ok=False, reason="validate: " + "; ".join(issues[:4]), draft=j))
            continue
        f = tg_post.ensure_comment({"comment": j.get("comment") or "", "headline": ev["post"]["text"][:60]},
                                   ev["post"]["text"])
        body = render(f.get("comment") or "", j["lines"], ev)
        decision = media_for(ev) if c["photo"] and tg_post.plain_len(body) <= 1000 else {
            "url": None, "approved": False, "provenance": None, "rejected": [], "preview": False}
        photo = decision.get("url") if decision.get("approved") else None
        d = dup(ev, target, now, c)
        pick = dict(info, ok=d is None, reason=why if d is None else "dup: " + d, topic=tp, target=target,
                    text=body, photo=photo)
        out["checked"].append(pick)
        if d:
            continue
        out["picks"].append(pick)
        if not dry_run:
            if shared_today(target, now) >= int(c["shared_daily_cap"]):
                out["posted"][target] = {"reason": f"storyline+forward shared_daily_cap {c['shared_daily_cap']}"}
                break
            why_c = tg_curate.check("storyline", now, entity=subj, channel=target)
            if why_c:
                out["posted"][target] = {"reason": "tg curate: " + why_c}
                break
            ref = f"story:{subj}:{ev['post']['id']}"
            raw = ev["post"].get("raw") if isinstance(ev["post"].get("raw"), dict) else {}
            entry = {"at": db.iso(now), "subject": subj, "url": ev["post"]["url"], "views": ev["post"]["views"],
                     "vph": ev["vph"], "interactions": list(ev["inter"].keys()), "text": body,
                     "source_text": ev["post"].get("text") or "", "author": subj, "media": decision,
                     "quoted_text": str((raw.get("quote") or {}).get("text") or "")}
            res = tg_route.deliver("storyline", body, targets_=[target], tp=tp, photo=photo, parse_mode="HTML",
                                   entity=subj, ref=ref, kv_log=KV_LOG, entry=entry, url=ev["post"]["url"],
                                   headline=ev["post"]["text"].split("\n")[0][:100], now=now)
            out["posted"][target] = {k: res.get(k) for k in ("ok", "sent", "queued", "skipped", "reason")}
            break
        if len(out["picks"]) >= top:
            break
    if not dry_run:
        db.kv_set(KV_SEEN, dict(list(seen.items())[-1000:]))
    return out


def status() -> dict:
    c = cfg()
    return {"enabled": c["enabled"],
            "log": [(x.get("at", "")[5:16], x.get("ref"), x.get("sent")) for x in (db.kv_get(KV_LOG, []) or [])[-6:]]}
