"""2026-11-02 tggrowth: Telegram channel routing + ONE central send function.

Owner decision: stop mirroring every post to every channel.
  @cblaboratory (크바연)  = AI-agent channel: agent frameworks, coding agents (Claude Code, Codex, Cursor, Devin ...),
                           lab/model launches relevant to agents, AI dev tools, agent infra, crypto x agent crossovers
                           (x402, agent payments, onchain agents). No general crypto price/exchange news.
  @blockschool (블록스쿨) = crypto (exchanges, on-chain, hacks, regulation) + macro (Fed/FOMC, CPI/jobs/PCE/GDP,
                           rates, central banks, treasury; official sources only).
  devgroup NEWS topic    = AI-agent + crypto breaking/hot (never macro / forward / weekly).
  Only a rare importance-5 story that is clearly agent AND crypto ("both") goes to both public channels.
  General AI news (not agent/dev relevant) -> @cblaboratory only at importance >= ai_general_min_importance.

topic(x)                      -> "agent" | "crypto" | "macro" | "ai_general" | "both" | None (deterministic first,
                                 optional cheap LLM fallback when ambiguous: tg_route.llm_fallback)
targets(lane, topic, imp, fp) -> [target_key, ...] from tg_route.map (target aliases agent/crypto/devgroup)
send(lane, targets, body, ...) -> {target: result}; every send is appended to kv tg_sent_log
                                 ({at, channel, lane, kind, entity, topic, ref, message_id, ok}); tg_curate counts it
deliver(...)                  -> routing + tg_curate per channel + tg_timing (send now / queue) + lane kv log update
targets.yaml tg_route.enabled: false -> old behaviour (legacy lanes go to every channel; new lanes still use the map).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime

from .. import accounts, config, db

log = logging.getLogger("yb.tgroute")

KV_SENT = "tg_sent_log"
KV_SINCE = "tg_sent_log_since"     # first tg_sent_log entry: older posts come from the legacy (mirrored) sources
KV_MSG = "tg_msg_ids"              # item_id -> {channel: message_id}
KV_TOPIC = "tg_route_topic_cache"
LEGACY_LANES = ("breaking", "hot", "report", "geeknews", "data", "manual")
FORWARD_LANES = ("forward", "storyline")   # 2026-11-02 tggrowth storyline: forward-kind (forward_cap)
TOPICS = ("agent", "crypto", "macro", "ai_general", "both")

DEFAULT_MAP = {"agent": ["agent", "devgroup"], "crypto": ["crypto", "devgroup"], "macro": ["crypto"],
               "both": ["agent", "crypto", "devgroup"], "ai_general": ["agent", "devgroup"]}

# ------------------------------------------------------------------ topic words (deterministic)
CRYPTO_ENTS = {"binance", "coinbase", "bybit", "okx", "bitget", "kraken", "upbit", "bithumb", "hyperliquid", "tether",
               "circle", "bitcoin", "ethereum", "solana", "sec", "blackrock", "strategy"}
AGENT_ENTS = {"openai", "anthropic", "cursor"}
AI_ENTS = {"google", "xai", "meta", "nvidia", "deepseek", "karpathy", "minimax", "mistral", "qwen"}
MACRO_ENTS = {"fed", "treasury", "bls", "bea", "ecb", "boj", "bok"}
MACRO_HANDLES = {"federalreserve", "bls_gov", "ustreasury", "bea_news", "ecb", "bank_of_japan_e"}
CRYPTO_HANDLES = {"binance", "cz_binance", "heyibinance", "coinbase", "brian_armstrong", "bybit_official", "okx",
                  "krakenfx", "bitgetglobal", "upbit_official", "tether", "paoloardoino", "circle", "hyperliquidx",
                  "secgov", "vitalikbuterin", "ethereum", "solana", "saylor", "lookonchain", "embercn", "zachxbt",
                  "peckshieldalert", "wublockchain", "defillama", "glassnode", "kaikodata", "cryptoquant_com",
                  "nansen_ai", "arkham", "spotonchain", "onchainlens"}
AGENT_HANDLES = {"openai", "openaidevs", "chatgptapp", "sama", "thsottiaux", "anthropicai", "claudeai", "alexalbert__",
                 "bcherny", "cursor_ai", "cognition_labs", "windsurf_ai", "github", "code", "replit", "vercel",
                 "langchainai", "llama_index", "openrouterai", "testingcatalog", "btibor91"}

CRYPTO_RE = re.compile(
    r"\b(?:binance|upbit|bithumb|coinbase|okx|bybit|bitget|kraken|hyperliquid|exchange hack|hack(?:ed|er|s)?|"
    r"exploit(?:ed)?|drain(?:ed)?|stolen funds|etfs?|sec\b|stablecoins?|usdt|usdc|tether|on-?chain|defi|dex|cex|"
    r"bitcoin|btc|ethereum|eth\b|solana|sol\b|altcoins?|token(?:s)? (?:unlock|listing)|listing|delist|airdrop|"
    r"wallet drain|crypto|blockchain|memecoins?|l2|rollup|validator|staking|liquidat(?:ion|ed)|funding rate|"
    r"open interest|spot etf|mica|cftc)\b|바이낸스|업비트|빗썸|코인베이스|거래소|해킹|탈취|익스플로잇|스테이블코인|"
    r"온체인|비트코인|이더리움|솔라나|알트코인|상장|상폐|에어드랍|디파이|가상자산|암호화폐|코인|청산|김치 ?프리미엄", re.I)
AGENT_RE = re.compile(
    r"\b(?:ai agents?|agents?|agentic|coding agents?|claude code|codex|cursor|devin|windsurf|copilot|gemini cli|"
    r"mcp|model context protocol|sdks?|apis?|devtools?|dev tools?|cli\b|ide\b|vibe coding|openai|anthropic|claude|"
    r"chatgpt|gpt-?\d[\w.]*|o\d\b|opus|sonnet|haiku|mythos|agent sdk|langchain|llamaindex|autogen|crewai|"
    r"computer use|tool use|function calling|swe-?bench|terminal-?bench|openrouter|replit|lovable|bolt\.new|"
    r"github|vercel|x402|a2a)\b|에이전트|코딩 ?에이전트|클로드 ?코드|코덱스|커서|개발자|개발 ?도구|바이브 ?코딩|"
    r"오픈ai|앤트로픽|클로드|챗gpt", re.I)
CROSS_RE = re.compile(
    r"\b(?:x402|agent(?:ic)? payments?|ai agents? (?:wallets?|payments?|trading|on-?chain)|on-?chain agents?|"
    r"agent wallets?|agentic commerce|ai x crypto|crypto x ai|agent tokens?|agentkit|virtuals protocol|ai16z|"
    r"elizaos|autonolas|olas|fetch\.ai)\b|에이전트 ?(?:결제|지갑|페이먼트)|온체인 ?에이전트", re.I)
MACRO_RE = re.compile(
    r"\b(?:fed|fomc|federal reserve|powell|rate (?:cut|hike|decision)s?|interest rates?|policy rate|"
    r"cpi|ppi|pce|core inflation|inflation|nonfarm|non-farm|payrolls?|jobs report|unemployment rate|employment situation|"
    r"gdp|treasur(?:y|ies)|yields?|10-?year|2-?year|t-?bills?|refunding|dxy|dollar index|ecb|lagarde|boj|bank of japan|"
    r"ueda|bok|bank of korea|central banks?|monetary policy|quantitative (?:easing|tightening)|qe|qt|bls|bea|"
    r"recession|tariffs?)\b|연준|기준금리|금리 ?(?:인하|인상|동결|결정)|소비자물가|물가|고용 ?(?:지표|보고서)|비농업|실업률|"
    r"국채|국고채|금리|한국은행|한은|일본은행|일은|유럽중앙은행|ECB|파월|우에다|라가르드|달러 ?인덱스|GDP|PCE|CPI|"
    r"통화정책|재무부|관세", re.I)
AI_RE = re.compile(r"\b(?:ai|llms?|models?|gemini|grok|llama|deepseek|qwen|kimi|mistral|minimax|nvidia|gpus?|"
                   r"inference|training|benchmark|open[- ]?weights?|reasoning model|frontier model|deepmind|xai)\b|"
                   r"인공지능|모델|딥마인드|제미나이|그록|엔비디아", re.I)


def cfg() -> dict:
    c = dict(config.targets().get("tg_route") or {})
    c.setdefault("enabled", False)
    ch = dict(c.get("channels") or {})
    ch.setdefault("agent", accounts.tg_channels()["agent"])
    ch.setdefault("crypto", accounts.tg_channels()["crypto"])
    ch.setdefault("devgroup", accounts.tg_channels()["devgroup"])
    c["channels"] = ch
    m = dict(DEFAULT_MAP)
    m.update(accounts.get("telegram.map", {}))
    m.update(c.get("map") or {})
    c["map"] = m
    c.setdefault("ai_general_min_importance", 5)
    c.setdefault("both_min_importance", 5)
    c.setdefault("devgroup_lanes", accounts.get("telegram.devgroup_lanes"))
    c.setdefault("llm_fallback", False)
    c.setdefault("log_keep", 2000)
    return c


def enabled() -> bool:
    return bool(cfg()["enabled"])


def _key(x) -> str:
    from . import tg_post
    return tg_post.target_key(x)


def alias(name: str) -> str:
    """'agent' | 'crypto' | 'devgroup' -> target key; anything else is taken as a target key itself."""
    ch = cfg()["channels"]
    return _key(ch[name]) if name in ch else _key(name)


def public_channels() -> list[str]:
    """Capped public channels (tg_curate counts per channel). Legacy tg_channel included."""
    from . import ai_news
    out = [alias("agent"), alias("crypto"), ai_news.tg_channel()]
    out += [k for k in _legacy_all() if k.startswith("@")]
    return [k for k in dict.fromkeys(out) if k]


def devgroup() -> str:
    return alias("devgroup")


def is_public(target: str) -> bool:
    return str(target) in public_channels()


def _legacy_all() -> list[str]:
    from . import ai_news, tg_post
    out = [ai_news.tg_channel()] + tg_post.extra_targets()
    return [c for c in dict.fromkeys(out) if c]


def channel_for_topic(topic: str | None) -> str | None:
    """The public channel a topic lives in (agent/ai_general -> agent channel; crypto/macro -> crypto channel)."""
    if topic in ("agent", "ai_general", "both"):
        return alias("agent")
    if topic in ("crypto", "macro"):
        return alias("crypto")
    return None


# ------------------------------------------------------------------ topic
def _blob(x) -> tuple[str, set[str], str]:
    """(text, entities, author handle) from a breaking story / ai_news_items row / forward post / plain string."""
    if isinstance(x, str):
        return x, set(), ""
    x = x or {}
    ents: set[str] = set()
    for k in ("entity", "_entity"):
        if x.get(k):
            ents.add(str(x[k]).lower())
    keys = x.get("keys")
    if isinstance(keys, str):
        try:
            keys = json.loads(keys)
        except ValueError:
            keys = []
    keys = [str(k).lower() for k in (keys or []) if k and not str(k).startswith("s:")]
    if keys and keys[0] == "data":
        keys = keys[1:]
    if keys:
        ents.add(keys[0])
    key = str(x.get("key") or x.get("run_id") or "")
    if key.startswith("breaking:"):
        key = key[9:]
    if key and ":" in key and not key.startswith(("report:", "s:")):
        ents.add(key.split(":")[0].lower())
    au = str(x.get("quote_author") or x.get("author_username") or x.get("author") or "").lower().lstrip("@")
    if not au:
        m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)/status", str(x.get("quote_url") or x.get("url") or ""))
        au = m.group(1).lower() if m else ""
    parts = [str(x.get(k) or "") for k in ("company", "headline", "primary_text", "text", "title", "summary",
                                           "tg_text", "x_text", "entity", "event")]
    for m in (x.get("posts") or [])[:3]:
        if str(m.get("id")) == str(x.get("quote_id")) or len(parts) < 12:
            parts.append(str(m.get("text") or "")[:600])
    return re.sub(r"https?://\S+", " ", " ".join(p for p in parts if p)), ents - {"", "data"}, au


LAB_WORDS = {"openai", "anthropic", "claude", "chatgpt", "오픈ai", "앤트로픽", "클로드", "챗gpt"}
POLICY_RE = re.compile(r"\b(?:regulat\w*|lobby\w*|lawsuits?|sued|election|midterms?|policy|politic\w*|"
                       r"lawmakers?|congress|senate|antitrust)\b|규제|로비|소송|선거|정치|의회|입법|반독점|홍보", re.I)


def scores(x) -> dict:
    text, ents, au = _blob(x)
    sc = {"agent": 0.0, "crypto": 0.0, "macro": 0.0, "ai": 0.0, "cross": 0.0}
    sc["agent"] += 3 * len(ents & AGENT_ENTS) + (3 if au in AGENT_HANDLES else 0)
    sc["crypto"] += 3 * len(ents & CRYPTO_ENTS) + (3 if au in CRYPTO_HANDLES else 0)
    sc["macro"] += 3 * len(ents & MACRO_ENTS) + (4 if au in MACRO_HANDLES else 0)
    sc["ai"] += 2 * len(ents & AI_ENTS)
    sc["agent"] += min(4, len(set(m.lower() for m in AGENT_RE.findall(text))))
    sc["crypto"] += min(4, len(set(m.lower() for m in CRYPTO_RE.findall(text))))
    sc["macro"] += min(4, len(set(m.lower() for m in MACRO_RE.findall(text))))
    sc["ai"] += min(3, len(set(m.lower() for m in AI_RE.findall(text))))
    sc["cross"] = float(len(set(m.lower() for m in CROSS_RE.findall(text))))
    # lab politics / PR / lawsuits (no product, agent or dev-tool word) = general AI news, not agent news
    hits = set(m.lower() for m in AGENT_RE.findall(text))
    sc["lab_policy"] = bool(POLICY_RE.search(text)) and bool(hits & LAB_WORDS) and not (hits - LAB_WORDS)
    return sc


def topic_det(x) -> tuple[str | None, bool]:
    """(topic, ambiguous). Deterministic: entity/handle (weight 3) + keyword hits."""
    s = scores(x)
    a, c, m, ai = s["agent"], s["crypto"], s["macro"], s["ai"]
    if s["lab_policy"] and c < 2 and m < 3:
        return "ai_general", False
    if s["cross"] and a >= 1:
        return ("both" if c >= 3 else "agent"), False
    if m >= 3 and m >= c and a < 3:
        return "macro", False
    if a >= 2 and c >= 2:
        if a >= c + 2:
            return "agent", False
        if c >= a + 2:
            return "crypto", False
        return ("agent" if a >= c else "crypto"), True
    if c >= 2 or (c >= 1 and a == 0 and ai == 0 and m < 2):
        return ("macro" if m > c else "crypto"), c < 2
    if a >= 2:
        return "agent", False
    if m >= 2:
        return "macro", False
    if a >= 1:
        return "agent", True
    if ai >= 2:
        return "ai_general", False
    if ai >= 1:
        return "ai_general", True
    return None, True


TOPIC_SYSTEM = """Classify ONE Korean/English news item for Korean Telegram channels. Return JSON {"topic": "..."} with
topic one of: agent (AI agents, coding agents, AI dev tools, AI lab/model launches relevant to developers or agents,
crypto x agent crossovers), crypto (crypto exchanges, on-chain, hacks, crypto regulation, tokens), macro (central banks,
rates, inflation, jobs, GDP, treasury, official macro data), ai_general (other AI news), none (anything else)."""


def topic(x, use_llm: bool | None = None) -> str | None:
    t, amb = topic_det(x)
    c = cfg()
    if not amb or not (c["llm_fallback"] if use_llm is None else use_llm):
        return t
    text = _blob(x)[0][:1200]
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    cache = db.kv_get(KV_TOPIC, {}) or {}
    if h in cache:
        return cache[h] or None
    try:
        from . import llm
        j = llm.chat_json("tg_route_topic", TOPIC_SYSTEM, json.dumps({"item": text}, ensure_ascii=False),
                          max_tokens=40, temperature=0.0)
        lt = str(j.get("topic") or "").strip().lower()
        lt = lt if lt in TOPICS else ("" if lt == "none" else t or "")
    except Exception as e:
        log.info("topic llm fallback failed: %s", type(e).__name__)
        return t
    cache[h] = lt
    db.kv_set(KV_TOPIC, dict(list(cache.items())[-500:]))
    return lt or None


# ------------------------------------------------------------------ targets
def targets(lane: str, tp: str | None, importance: int = 4, first_party: bool = False) -> list[str]:
    """Target keys for a post. Disabled routing: legacy lanes -> every channel (old behaviour)."""
    c = cfg()
    imp = int(importance or 0)
    if not c["enabled"] and lane in LEGACY_LANES:
        return _legacy_all()
    if tp == "both" and imp < int(c["both_min_importance"]):
        tp = "agent"
    if tp == "ai_general" and imp < int(c["ai_general_min_importance"]) and lane not in ("weekly",):
        return []
    if tp not in TOPICS:
        return []
    out = [alias(a) for a in (c["map"].get(tp) or [])]
    dg = devgroup()
    if lane not in c["devgroup_lanes"] or tp == "macro":
        out = [t for t in out if t != dg]
    return [t for t in dict.fromkeys(out) if t]


def route(lane: str, x, importance: int = 4, first_party: bool = False) -> tuple[str | None, list[str]]:
    tp = topic(x)
    return tp, targets(lane, tp, importance, first_party)


# ------------------------------------------------------------------ sent log
def log_sent(entry: dict) -> None:
    lg = db.kv_get(KV_SENT, []) or []
    if not db.kv_get(KV_SINCE):
        db.kv_set(KV_SINCE, entry["at"])
    lg.append(entry)
    db.kv_set(KV_SENT, lg[-int(cfg()["log_keep"]):])


def sent_log() -> list[dict]:
    return db.kv_get(KV_SENT, []) or []


def record_msg(item_id, channel: str, mid: str) -> None:
    if not item_id or not mid:
        return
    m = db.kv_get(KV_MSG, {}) or {}
    m.setdefault(str(item_id), {})[channel] = str(mid)
    db.kv_set(KV_MSG, dict(list(m.items())[-500:]))


def msg_ids(item_id) -> dict:
    return (db.kv_get(KV_MSG, {}) or {}).get(str(item_id)) or {}


def kind_of(lane: str) -> str:
    return "forward" if lane in FORWARD_LANES else "core"


def send_one(lane: str, target: str, body: str, parse_mode: str | None = "HTML", photo: str | None = None,
             meta: dict | None = None, token: str | None = None) -> dict:
    """One Telegram send (tg_post.send) + tg_sent_log entry. Returns the Telegram JSON (+ 'via'), never raises for
    Telegram errors ({"ok": False, "description": ...})."""
    from . import ai_news, tg_post
    meta = meta or {}
    tok = token or ai_news._bot_token()
    if not tok:
        return {"ok": False, "description": "no bot token"}
    try:
        d = tg_post.send(tok, target, body, parse_mode, photo, quote=meta.get("quote"),
                         preview=not meta.get("no_preview"))   # 2026-11-05 anncap
    except Exception as e:
        d = {"ok": False, "description": f"{type(e).__name__}: {str(e).replace(tok, '***')[:160]}"}
    mid = str((d.get("result") or {}).get("message_id") or "") if d.get("ok") else ""
    log_sent({"at": db.iso(db.now_kst()), "channel": target, "lane": lane, "kind": kind_of(lane),
              "entity": str(meta.get("entity") or "").lower()[:40], "topic": meta.get("topic"),
              "ref": str(meta.get("ref") or "")[:200], "message_id": mid, "ok": bool(d.get("ok")),
              "error": None if d.get("ok") else str(d.get("description"))[:160], "via": d.get("via"),
              "url": str(meta.get("url") or "")[:300], "headline": str(meta.get("headline") or "")[:120]})
    if d.get("ok") and meta.get("item_id"):
        record_msg(meta["item_id"], target, mid)
    return d


def send(lane: str, targets_: list[str], body: str, parse_mode: str | None = "HTML", photo: str | None = None,
         meta: dict | None = None, dry_run: bool = False) -> dict:
    """THE central TG send: {target: {"ok", "message_id", "via", "error"}}. dry_run sends and logs nothing."""
    out = {}
    for t in targets_:
        if dry_run:
            out[t] = {"ok": True, "dry_run": True, "message_id": "", "via": "dry"}
            continue
        d = send_one(lane, t, body, parse_mode, photo, meta)
        out[t] = {"ok": bool(d.get("ok")), "via": d.get("via"),
                  "message_id": str((d.get("result") or {}).get("message_id") or "") if d.get("ok") else "",
                  "error": None if d.get("ok") else str(d.get("description"))[:160]}
    return out


# ------------------------------------------------------------------ lane kv logs (hot/report/gn entries)
def lane_log_set(kv: str | None, ref: str, target: str, value: str) -> None:
    if not kv or not ref:
        return
    lg = db.kv_get(kv, []) or []
    for x in reversed(lg):
        if x.get("ref") == ref:
            x.setdefault("sent", {})[target] = value
            db.kv_set(kv, lg)
            return


def _res_str(r: dict) -> str:
    return (str(r.get("message_id") or "") + f" ({r.get('via')})") if r.get("ok") else "err: " + str(r.get("error"))[:120]


def _deliver_one(lane: str, t: str, body: str, parse_mode, photo, meta: dict) -> dict:
    """Send to one target; the first public target of an ai_news item goes through ai_news.post_tg (tg_posted_at)."""
    from . import ai_news
    iid = meta.get("item_id")
    if iid:
        done = db.kv_get("breaking_tg_extra", {}) or {}
        k = f"{iid}:{t}"
        if k in done or t in msg_ids(iid):
            return {"ok": False, "error": "already sent to this channel", "dup": True}
        it = db.one("SELECT tg_posted_at FROM ai_news_items WHERE id=?", (iid,)) or {}
        if not it.get("tg_posted_at") and is_public(t):
            r = ai_news.post_tg(int(iid), text=body, parse_mode=parse_mode, photo=photo or "", chat=t, lane=lane,
                                meta=meta)
            done[k] = r.get("message_id") or ("err: " + str(r.get("error"))[:120])
            db.kv_set("breaking_tg_extra", done)
            return {"ok": bool(r.get("ok")), "message_id": r.get("message_id") or "", "via": r.get("via"),
                    "error": r.get("error")}
        done[k] = "pending"
        db.kv_set("breaking_tg_extra", done)
        r = send(lane, [t], body, parse_mode, photo, meta)[t]
        done[k] = r.get("message_id") or ("err: " + str(r.get("error"))[:120])
        db.kv_set("breaking_tg_extra", done)
        return r
    return send(lane, [t], body, parse_mode, photo, meta)[t]


def deliver(lane: str, body: str, *, targets_: list[str] | None = None, tp: str | None = None, x=None,
            photo: str | None = None, parse_mode: str | None = "HTML", importance: int = 4,
            first_party: bool = False, entity: str = "", ref: str = "", s: dict | None = None,
            item_id: int | None = None, urgent: bool = False, kv_log: str | None = None, entry: dict | None = None,
            url: str = "", headline: str = "", now: datetime | None = None, dry_run: bool = False,
            hold_h: float | None = None, quote: dict | None = None) -> dict:
    """Route + curate (per channel) + timing (send now or queue) + send. Claims the lane kv entry first.
    Returns {"ok", "topic", "targets", "sent": {t: res}, "queued": [t], "skipped": {t: why}}."""
    from . import tg_curate, tg_timing
    now = now or db.now_kst()
    if first_party and int(importance or 0) >= 4 and lane not in FORWARD_LANES:
        urgent = True   # 2026-11-02 newsfix: primary-source importance 4-5 news goes immediately
    if tp is None and x is not None:
        tp = topic(x)
    tg = list(targets_) if targets_ is not None else targets(lane, tp, importance, first_party)
    out = {"ok": False, "topic": tp, "targets": tg, "sent": {}, "queued": [], "skipped": {}}
    if not tg:
        out["reason"] = f"no target for topic {tp!r} (lane {lane}, importance {importance})"
        return out
    ok_t, skipped = tg_curate.filter_targets(lane, tg, now, entity=entity, first_party=first_party,
                                             importance=importance, s=s)
    out["skipped"] = skipped
    if not ok_t:
        out["reason"] = "tg curate: " + "; ".join(f"{k}: {v}" for k, v in skipped.items())[:300]
        return out
    meta = {"entity": entity, "topic": tp, "ref": ref, "item_id": item_id, "url": url, "headline": headline}
    if quote:
        meta["quote"] = quote   # 2026-11-03 tgquote: forward lane external reply to the source post
    if entry is not None and kv_log and not dry_run:
        entry["ref"] = ref
        entry["topic"] = tp
        entry["sent"] = {}
        lg = db.kv_get(kv_log, []) or []
        lg.append(entry)
        db.kv_set(kv_log, lg[-300:])
    for t in ok_t:
        when = tg_timing.decide(lane, t, now, urgent=urgent, importance=int(importance or 0),
                                first_party=bool(first_party))
        if when == "queue":
            if not dry_run:
                tg_timing.enqueue({"lane": lane, "target": t, "body": body, "photo": photo, "parse_mode": parse_mode,
                                   "importance": importance, "first_party": first_party, "urgent": urgent,
                                   "kv_log": kv_log, **meta}, now, hold_h=hold_h)
                lane_log_set(kv_log, ref, t, "queued")
            out["queued"].append(t)
            continue
        if dry_run:
            out["sent"][t] = {"ok": True, "dry_run": True}
            continue
        r = _deliver_one(lane, t, body, parse_mode, photo, meta)
        out["sent"][t] = r
        lane_log_set(kv_log, ref, t, _res_str(r))
    out["ok"] = any(r.get("ok") for r in out["sent"].values()) or bool(out["queued"])
    log.info("tg deliver [%s] %s topic=%s -> sent=%s queued=%s skipped=%s", lane, ref, tp,
             {k: v.get("ok") for k, v in out["sent"].items()}, out["queued"], skipped)
    return out


def status() -> dict:
    c = cfg()
    lg = sent_log()
    day = db.kst_day()
    today = [x for x in lg if str(x.get("at", ""))[:10] == day and x.get("ok")]
    per = {}
    for x in today:
        per.setdefault(x["channel"], []).append(x["lane"])
    return {"enabled": c["enabled"], "channels": c["channels"], "since": db.kv_get(KV_SINCE),
            "today": per, "last": lg[-8:]}
