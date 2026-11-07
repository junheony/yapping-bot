"""Telegram newsworthiness, grounding, dedup, Korean style, and media fail-closed.

Network and Telegram sends stay in the service modules. This module only decides.
Author or employer fame never adds evidence.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Org accounts. People who work there are intentionally absent: fame is not a source.
OFFICIAL_ORG_HANDLES = {
    "openai", "openaidevs", "chatgptapp", "anthropicai", "claudeai", "cursor_ai",
    "github", "binance", "coinbase", "okx", "krakenfx", "bybit_official",
    "upbit_official", "federalreserve", "secgov",
}
OFFICIAL_HOST_SUFFIXES = (
    "openai.com", "anthropic.com", "cursor.com", "github.com", "x.ai",
    "blog.google", "binance.com", "coinbase.com", "okx.com", "kraken.com",
    "federalreserve.gov", "sec.gov",
)
GAME_HOSTS = {"poteto.com", "itch.io", "crazygames.com", "poki.com"}
SECRET_QUERY_KEYS = {"token", "key", "secret", "sig", "signature", "auth", "password", "pass", "cookie", "access_token"}
TRACKING_QUERY_KEYS = SECRET_QUERY_KEYS | {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "ref", "ref_src", "s", "si", "fbclid", "gclid", "mc_cid", "mc_eid",
}

THUMB_URL_RE = re.compile(
    r"amplify_video_thumb|ext_tw_video_thumb|tweet_video_thumb|video_thumb|pu/img/tweet_video",
    re.I,
)
PLACEHOLDER_URL_RE = re.compile(
    r"placeholder|default_profile_images|spacer|blank\.(?:gif|png)|/emoji/|1x1",
    re.I,
)
STATUS_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x|twitter|fxtwitter|vxtwitter|fixupx)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_@])@([A-Za-z0-9_]{2,15})")
HANGUL_RE = re.compile(r"[가-힣]")
EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\u2190-\u21FF"
    "\u2B00-\u2BFF"
    "\uFE0F"
    "\u200D"
    "]+",
    flags=re.UNICODE,
)
BOLD_RE = re.compile(r"</?(?:b|strong|u|i)>|\*\*", re.I)
TRIVIAL_RE = re.compile(
    r"get me out of the basement|it'?s friday|happy friday|just a joke|shitpost|"
    r"for (?:the )?laughs|\bmini[- ]?game\b|\bbrowser game\b|\bplay (?:this|the) game\b|"
    r"장난(?:으로|임|이다)?|개그|웃기려고|드립(?:이다|임)?",
    re.I,
)
PR_ONLY_RE = re.compile(
    r"\b(?:excited to announce|thrilled to|proud to partner|we(?:'|’)re hiring|check out my)\b|"
    r"기대(?:된다|해주세요)|많은 관심",
    re.I,
)
NEWS_RE = re.compile(
    r"\b(?:introduc\w+|launch\w*|ship(?:s|ped|ping)?|releas\w+|announc\w+|available|roll(?:ing|ed) out|"
    r"open[- ]sources?|priced?|benchmark|cve-\d+|exploit|hack(?:ed)?|drained|stolen|delist\w*|"
    r"will list|lists|listing|regulat\w+|acqui(?:re|res|red)|raises?|cuts?|hikes?|holds?|"
    r"unveils?|adds? support|now supports|can now)\b|"
    r"출시|공개|발표|상장|해킹|규제|금리|지원(?:한다|된다)|쓸 수 있다",
    re.I,
)
PRACTICAL_RE = re.compile(
    r"\b(?:you can|lets you|so you can|can now|without having to|instead of)\b|"
    r"수 있다|하면 된다|쓸 수 있다|물어보면|이어서",
    re.I,
)
CONCRETE_RE = re.compile(
    r"\d|v\d+|gpt-?\d|claude|cursor|muse|voice|https?://|\$[A-Za-z]{2,}|\b[A-Z][A-Za-z0-9]{2,}\b|"
    r"[가-힣]{2,}",
)
AI_CLAIM_RE = re.compile(
    r"openai|오픈\s*AI|오픈AI|gpt-?\d|에이전트|ai[- ]made|made with ai|인공지능|모델(?:을|를)? 출시",
    re.I,
)
AI_EVIDENCE_RE = re.compile(
    r"openai|gpt-?\d|anthropic|claude|\bagent\b|\bmodel\b|\bllm\b|\bai\b|인공지능|에이전트",
    re.I,
)
FIRSTHAND_RE = re.compile(
    r"써\s*봤|직접\s*(?:써|해\s*봤)|내가\s*써|테스트해\s*봤|내가\s*해\s*보|내가 써본|"
    r"\b(?:i|we)\s+(?:tried|tested|used)\b|in my testing",
    re.I,
)
PROMO_RE = re.compile(
    r"airdrop|referral code|giveaway|whitelist|use my (?:code|link)|retweet and follow|"
    r"에어드랍|레퍼럴|초대\s*코드",
    re.I,
)
REPORT_RE = re.compile(
    r"(?:짚었|설명했|지적했|전망했|강조했|밝혔|언급했|덧붙였|소개했|평가했|주장했|분석했|내다봤)"
    r"(?:다|음|고|으며|는데|던)?(?=$|[\s.,!?])"
)
BANNED_ENDING_RE = re.compile(
    r"(?:(?<!동)(?<!아테)네|임|함|요|죠|습니다|입니다|군요|네요|(?:했|였|있|없|되|된|같|렸|냈|봤)음)$"
)
LAST_WORD_ALLOW = {"포함", "위험", "다음", "마음", "처음", "중심", "핵심", "책임", "방침", "동네"}
LINK_LINE_RE = re.compile(r"^(?:원문 보기|원문|https?://\S+|@?[A-Za-z0-9_]{1,15} 원문)$")
PRIVILEGED_TIER_RE = re.compile(r"top_tier|:update\b|/update\b", re.I)
PRIVATE_TG_RE = re.compile(
    r"https?://t\.me/(?:c/\d+/\d+|\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+)",
    re.I,
)
PUBLIC_TG_RE = re.compile(r"https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)/?(?:[?#].*)?$", re.I)
PRIVATE_CHANNEL_RE = re.compile(r"^-?\d|^\d+#|t\.me/c/", re.I)

_ENFORCEMENT = {"status": "not_applied", "observed": False}


def set_enforcement_attestation(status: str | None, *, observed: bool = False) -> None:
    """Record a sender-process attestation. Missing or unobserved stays not_applied."""
    if status is None:
        _ENFORCEMENT["status"] = "not_applied"
        _ENFORCEMENT["observed"] = False
        return
    if status not in {"not_applied", "prepared", "testing", "live"}:
        raise ValueError("unknown enforcement status")
    _ENFORCEMENT["status"] = status
    _ENFORCEMENT["observed"] = bool(observed)


def enforcement_status() -> str:
    if not _ENFORCEMENT.get("observed"):
        return "not_applied"
    return str(_ENFORCEMENT.get("status") or "not_applied")


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return ""
    return host.lower().removeprefix("www.")


def _clean_query(url: str) -> str:
    parts = urlsplit(url)
    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if k.lower() in TRACKING_QUERY_KEYS:
            continue
        kept.append((k, v))
    path = parts.path.rstrip("/") or "/"
    if path == "/":
        path = ""
    return urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower(), path, urlencode(kept), ""))


def sanitize_source_url(url: str | None) -> str | None:
    """Credential-free https URL, or None. Userinfo and secret query keys are removed."""
    raw = str(url or "").strip()
    if not raw:
        return None
    raw = raw.rstrip(").,]}>\"'")
    if raw.startswith("http://"):
        return None
    if not raw.startswith("https://"):
        return None
    parts = urlsplit(raw)
    if parts.username or parts.password:
        raw = urlunsplit(("https", parts.hostname or "", parts.path, parts.query, ""))
        parts = urlsplit(raw)
    if not parts.hostname:
        return None
    return _clean_query(urlunsplit(("https", parts.hostname, parts.path, parts.query, "")))


def public_telegram_url(url: str | None) -> str | None:
    """https://t.me/<publichandle>/<id> only. Private links return None."""
    raw = str(url or "").strip()
    if not raw or PRIVATE_TG_RE.search(raw):
        return None
    m = PUBLIC_TG_RE.search(raw)
    if not m:
        return None
    return f"https://t.me/{m.group(1)}/{m.group(2)}"


def redact_channel(channel: str | None) -> str | None:
    text = str(channel or "").strip()
    if not text:
        return None
    if PRIVATE_CHANNEL_RE.search(text) or PRIVATE_TG_RE.search(text):
        return "private group"
    if text.startswith("@"):
        return text
    pub = public_telegram_url(text)
    if pub:
        return "@" + pub.split("/")[3]
    return text


def status_id(url: str | None) -> str | None:
    m = STATUS_RE.search(str(url or ""))
    return m.group(2) if m else None


def status_handle(url: str | None) -> str | None:
    m = STATUS_RE.search(str(url or ""))
    return m.group(1).lower() if m else None


def find_urls(text: str) -> list[str]:
    return [m.group(0) for m in URL_RE.finditer(text or "")]


def canonical_external(url: str) -> str | None:
    clean = sanitize_source_url(url)
    if not clean:
        return None
    if status_id(clean):
        return None
    host = _host(clean)
    if host in {"t.co", "x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com"}:
        return None
    return clean


def _norm_claim(text: str) -> str:
    t = re.sub(r"https?://\S+", " ", str(text or "").lower())
    t = re.sub(r"[^0-9a-z가-힣\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def story_keys(item: dict | None) -> list[str]:
    """Identity ignores the posting account and which image was attached."""
    item = item or {}
    keys: list[str] = []
    blobs = [item.get("url"), item.get("source_text"), item.get("text"), item.get("quoted_text"),
             item.get("headline"), item.get("ref"), item.get("body")]
    for extra in item.get("extra_urls") or []:
        blobs.append(extra)
    for blob in blobs:
        for url in find_urls(str(blob or "")) + ([str(blob)] if str(blob or "").startswith("http") else []):
            sid = status_id(url)
            if sid:
                keys.append(f"status:{sid}")
                continue
            ext = canonical_external(url)
            if ext:
                keys.append(f"url:{ext}")
    if item.get("story_id"):
        keys.append("id:" + str(item["story_id"])[:120])
    claim_src = item.get("claim") or item.get("source_text") or item.get("headline") or ""
    claim = _norm_claim(str(claim_src))
    if len(claim) >= 24 and re.search(r"[a-z0-9]{6,}|[가-힣]{6,}", claim):
        keys.append("claim:" + claim[:180])
    return list(dict.fromkeys(keys))


def duplicate_reason(keys: list[str], records: list[dict] | None, *, ignore_ref: str | None = None,
                     ignore_qid: str | None = None) -> str | None:
    if not keys:
        return None
    want = set(keys)
    for rec in records or []:
        if rec.get("ok") is False:
            continue
        if ignore_qid and rec.get("qid") and rec.get("qid") == ignore_qid:
            continue
        if ignore_ref and rec.get("ref") and rec.get("ref") == ignore_ref and not rec.get("ok"):
            continue
        have = set(rec.get("story_keys") or [])
        have.update(story_keys(rec))
        hit = want & have
        if hit:
            return "duplicate story via " + sorted(hit)[0]
    return None


def _kind_from_url(url: str, declared: str, origin: str) -> str:
    if THUMB_URL_RE.search(url or ""):
        return "gif_thumbnail" if "gif" in declared else "video_thumbnail"
    if PLACEHOLDER_URL_RE.search(url or ""):
        return "placeholder"
    if origin == "quote":
        return "quoted_photo"
    if declared in {"video_thumbnail", "gif_thumbnail", "quoted_photo", "own_photo", "placeholder", "generated_card"}:
        return declared
    if declared in {"video", "animated_gif", "gif"}:
        return "gif_thumbnail" if "gif" in declared else "video_thumbnail"
    if declared == "photo":
        return "own_photo"
    return "unknown"


def _add_candidate(out: list[dict], seen: set[str], url: str | None, kind: str, origin: str, alt: str = "") -> None:
    url = str(url or "").strip()
    if not url or url in seen:
        return
    seen.add(url)
    out.append({
        "url": url,
        "kind": _kind_from_url(url, kind, origin),
        "origin": origin,
        "alt": str(alt or ""),
        "verified_relevant": False,
    })


def candidates_from_fx(tweet: dict | None, origin: str = "original") -> list[dict]:
    tweet = tweet or {}
    med = tweet.get("media") or {}
    out: list[dict] = []
    seen: set[str] = set()
    for photo in med.get("photos") or []:
        if isinstance(photo, dict):
            _add_candidate(out, seen, photo.get("url"), "own_photo", origin,
                           photo.get("alt_text") or photo.get("alt") or "")
    for video in list(med.get("videos") or []) + list(med.get("gifs") or []) + list(med.get("animated_gifs") or []):
        if not isinstance(video, dict):
            continue
        declared = "gif_thumbnail" if "gif" in str(video.get("type") or "").lower() else "video_thumbnail"
        _add_candidate(out, seen, video.get("thumbnail_url") or video.get("preview_image_url") or video.get("url"),
                       declared, origin, video.get("alt_text") or "")
    for item in med.get("all") or []:
        if not isinstance(item, dict):
            continue
        typ = str(item.get("type") or "").lower()
        if typ in {"video", "gif", "animated_gif"}:
            declared = "gif_thumbnail" if "gif" in typ else "video_thumbnail"
            _add_candidate(out, seen, item.get("thumbnail_url") or item.get("preview_image_url") or item.get("url"),
                           declared, origin, item.get("alt_text") or "")
        elif typ == "photo":
            _add_candidate(out, seen, item.get("url"), "own_photo", origin, item.get("alt_text") or "")
    quote = tweet.get("quote") if origin != "quote" else None
    if isinstance(quote, dict) and quote.get("media"):
        for cand in candidates_from_fx({"media": quote.get("media")}, "quote"):
            if cand["url"] not in seen:
                seen.add(cand["url"])
                out.append(cand)
    return out


def candidates_from_post(post: dict | None, origin: str = "original") -> list[dict]:
    """Structured attachments only. Bare URL lists are unknown and fail closed later."""
    if not isinstance(post, dict):
        return []
    if isinstance(post.get("media"), dict):
        return candidates_from_fx(post, origin)
    media = post.get("media")
    if media is None:
        media = (post.get("extended_entities") or {}).get("media")
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(media, list):
        return out
    for item in media:
        if isinstance(item, str):
            _add_candidate(out, seen, item, "unknown", origin)
            continue
        if not isinstance(item, dict):
            continue
        typ = str(item.get("type") or "")
        url = item.get("url") or item.get("media_url_https") or item.get("preview_image_url") or item.get("thumbnail_url")
        _add_candidate(out, seen, url, typ or "unknown", origin, item.get("alt_text") or item.get("alt") or "")
    return out


def _tokens(text: str) -> set[str]:
    stop = {"this", "that", "with", "from", "your", "have", "will", "just", "like", "about", "into",
            "they", "them", "what", "should", "could", "image", "photo", "video", "screenshot", "http", "https"}
    words = re.findall(r"[a-z]{4,}|[가-힣]{2,}", (text or "").lower())
    return {w for w in words if w not in stop}


def alt_supports(alt: str, story: str) -> bool:
    left, right = _tokens(alt), _tokens(story)
    if not left or not right:
        return False
    for a in left:
        for b in right:
            short, long = (a, b) if len(a) <= len(b) else (b, a)
            if len(short) >= 4 and (short == long or long.startswith(short)):
                return True
    return False


def _reject(url: str, kind: str, reason: str) -> dict:
    return {"url": sanitize_source_url(url) or (url if url.startswith("/") or "tweetcap" in url else None),
            "kind": kind, "reason": reason}


def choose_media(candidates: list[dict] | None, *, story_text: str = "", logo_fn=None,
                 link: str = "", official_capture_handles: set[str] | None = None) -> dict:
    """Pick an official capture or a verified relevant demo. Otherwise text, preview off.

    Quote images and unchecked video/gif thumbnails are recorded and never selected.
    A racetrack (or any other) video frame is not a demo unless verification says so.
    Official tweet capture is offered only for a logo/brand card, not for a rejected video frame:
    a screenshot of that frame would republish the same unrelated image.
    """
    rejected = []
    handles = {h.lower() for h in (official_capture_handles or set())}
    handle = status_handle(link)
    allow_capture = False
    chosen = None
    for cand in candidates or []:
        url = str(cand.get("url") or "")
        kind = _kind_from_url(url, str(cand.get("kind") or ""), str(cand.get("origin") or "original"))
        origin = cand.get("origin") or "original"
        alt = str(cand.get("alt") or "")
        if origin == "quote" or kind == "quoted_photo":
            rejected.append({"url": sanitize_source_url(url), "kind": "quoted_photo", "reason": "quoted_post_image"})
            continue
        if kind in {"video_thumbnail", "gif_thumbnail"} or THUMB_URL_RE.search(url):
            reason = "unchecked_video_thumbnail" if kind != "gif_thumbnail" else "unchecked_video_thumbnail"
            if not cand.get("verified_relevant"):
                rejected.append({"url": sanitize_source_url(url), "kind": kind, "reason": reason})
                continue
        if kind in {"placeholder", "generated_card", "unknown"} or PLACEHOLDER_URL_RE.search(url):
            rejected.append({"url": sanitize_source_url(url), "kind": kind or "placeholder",
                             "reason": "placeholder" if kind != "unknown" else "provenance_missing"})
            continue
        if handle and handle in handles and kind == "own_photo":
            rejected.append({"url": sanitize_source_url(url), "kind": kind, "reason": "logo_or_placeholder"})
            allow_capture = True
            continue
        verified = bool(cand.get("verified_relevant"))
        if not verified and alt and story_text and alt_supports(alt, story_text):
            verified = True
        if not verified:
            reason = "unrelated_photo" if alt else "unverified_relevance"
            rejected.append({"url": sanitize_source_url(url), "kind": kind or "own_photo", "reason": reason})
            continue
        if logo_fn is not None:
            try:
                is_logo = bool(logo_fn(url))
            except Exception:
                is_logo = False
            if is_logo:
                rejected.append({"url": sanitize_source_url(url), "kind": kind, "reason": "logo_or_placeholder"})
                allow_capture = True
                continue
        if chosen is None:
            chosen = {"url": sanitize_source_url(url) or url, "provenance": "verified_demo", "kind": "own_photo"}
        continue
    if any(row.get("reason") == "unchecked_video_thumbnail" for row in rejected):
        # A tweet screenshot would republish the same unchecked frame.
        allow_capture = False
    if chosen and chosen.get("url"):
        return {"url": chosen["url"], "provenance": "verified_demo", "approved": True, "preview": False,
                "rejected": rejected, "allow_official_capture": False, "reason": None}
    return {"url": None, "provenance": None, "approved": False, "preview": False, "rejected": rejected,
            "allow_official_capture": allow_capture, "reason": (rejected[0]["reason"] if rejected else "no_supported_media")}


def gate_photo_url(url: str | None, *, provenance: str | None = None, story_text: str = "",
                   alt: str = "", verified_relevant: bool = False, origin: str | None = None,
                   kind: str | None = None) -> dict:
    """Legacy and final-send choke point. A bare URL is not provenance."""
    raw = str(url or "").strip()
    if not raw:
        return {"url": None, "provenance": None, "approved": False, "preview": False, "rejected": [],
                "allow_official_capture": False, "reason": None}
    declared = kind or ("quoted_photo" if origin == "quote" or provenance in {"quote", "quoted_post_image"} else "unknown")
    cand = {"url": raw, "kind": declared, "origin": origin or ("quote" if provenance in {"quote", "quoted_post_image"} else "original"),
            "alt": alt, "verified_relevant": verified_relevant or provenance == "verified_demo"}
    if provenance == "official_capture" and not THUMB_URL_RE.search(raw) and "quoted" not in declared:
        return {"url": raw, "provenance": "official_capture", "approved": True, "preview": False, "rejected": [],
                "allow_official_capture": False, "reason": None}
    if provenance == "verified_demo":
        cand["verified_relevant"] = True
        cand["kind"] = "own_photo" if declared == "unknown" else declared
    decision = choose_media([cand], story_text=story_text)
    if provenance in {None, "", "unknown"} and decision.get("url") and not verified_relevant:
        decision = {"url": None, "provenance": None, "approved": False, "preview": False,
                    "rejected": [{"url": sanitize_source_url(raw), "kind": declared, "reason": "provenance_missing"}],
                    "allow_official_capture": False, "reason": "provenance_missing"}
    return decision


def _author(item: dict) -> str:
    author = str(item.get("author") or item.get("author_username") or item.get("entity") or "").lower().lstrip("@")
    if author:
        return author
    return status_handle(item.get("url") or "") or ""


def _evidence_text(item: dict) -> str:
    return "\n".join(str(item.get(k) or "") for k in ("source_text", "quoted_text", "primary_text") if item.get(k))


def _claim_text(item: dict) -> str:
    return "\n".join(str(item.get(k) or "") for k in ("body", "headline", "text", "comment") if item.get(k))


def _urls_of(item: dict, blob: str) -> list[str]:
    urls = list(item.get("extra_urls") or [])
    if item.get("url"):
        urls.append(str(item["url"]))
    urls.extend(find_urls(blob))
    return urls


def _official_url(urls: list[str]) -> bool:
    for url in urls:
        host = _host(url)
        if not host:
            continue
        if any(host == suf or host.endswith("." + suf) for suf in OFFICIAL_HOST_SUFFIXES):
            return True
    return False


def _game_url(urls: list[str]) -> bool:
    return any(_host(url) in GAME_HOSTS for url in urls)


def _substantive(text: str) -> bool:
    if not text or not text.strip() or TRIVIAL_RE.search(text):
        return False
    if PROMO_RE.search(text) or PR_ONLY_RE.search(text) and not PRACTICAL_RE.search(text):
        return False
    if NEWS_RE.search(text) and (CONCRETE_RE.search(text) or PRACTICAL_RE.search(text)):
        return True
    if PRACTICAL_RE.search(text) and CONCRETE_RE.search(text):
        return True
    return False


def _privileged(item: dict) -> bool:
    blob = " ".join(str(item.get(k) or "") for k in ("classification", "tier", "category", "key", "ref"))
    return bool(PRIVILEGED_TIER_RE.search(blob))


def is_aggregator(item: dict) -> bool:
    if item.get("aggregator") or item.get("discovery_only"):
        return True
    if str(item.get("author_role") or "").lower() in {"aggregator", "relay", "mirror"}:
        return True
    author = _author(item)
    names = {str(h).lower().lstrip("@") for h in (item.get("aggregator_handles") or [])}
    if author and author in names:
        return True
    try:
        from ..services.sourcing import is_relay
        return bool(is_relay({"author_username": author, "text": _evidence_text(item)}))
    except Exception:
        return False


def assess_newsworthiness(item: dict | None) -> dict:
    """Allow important news, product updates, and practical cases grounded in a primary source.

    Aggregator copy is discovery-only. A privileged tier string is not evidence.
    """
    item = item or {}
    if str(item.get("lane") or "") == "manual":
        return {"allow": True, "reason": None, "kind": "manual", "evidence": "exempt"}
    evidence = _evidence_text(item)
    claim = _claim_text(item)
    blob = evidence or claim
    urls = _urls_of(item, "\n".join([evidence, claim, str(item.get("url") or "")]))
    author = _author(item)
    trivial = bool(TRIVIAL_RE.search(evidence) or TRIVIAL_RE.search(claim) or (_game_url(urls) and not _substantive(evidence)))
    if trivial and not (_substantive(evidence) and (_official_url(urls) or author in OFFICIAL_ORG_HANDLES)):
        return {"allow": False, "reason": "not_newsworthy_trivial", "kind": None,
                "evidence": "source" if evidence else "missing"}
    if PROMO_RE.search(evidence or claim):
        return {"allow": False, "reason": "not_newsworthy_promo", "kind": None,
                "evidence": "source" if evidence else "missing"}
    if (PR_ONLY_RE.search(evidence or claim)) and not _substantive(evidence or ""):
        return {"allow": False, "reason": "not_newsworthy_pr", "kind": None,
                "evidence": "source" if evidence else "missing"}
    if evidence and AI_CLAIM_RE.search(claim) and not AI_EVIDENCE_RE.search(evidence):
        return {"allow": False, "reason": "invented_ai_context", "kind": None, "evidence": "source"}
    if evidence:
        visible = URL_RE.sub(" ", claim)
        for number in re.findall(r"\d[\d.,]*%?", visible):
            if number not in evidence and number.replace(",", "") not in evidence:
                return {"allow": False, "reason": "ungrounded_number", "kind": None, "evidence": "source"}
    if FIRSTHAND_RE.search(claim) and not FIRSTHAND_RE.search(evidence):
        return {"allow": False, "reason": "invented_firsthand", "kind": None,
                "evidence": "source" if evidence else "missing"}
    if is_aggregator(item):
        quoted_official = author in OFFICIAL_ORG_HANDLES  # aggregator's own handle is not official
        official = _official_url(urls) or any(status_handle(u) in OFFICIAL_ORG_HANDLES for u in urls)
        if not (official and _substantive(evidence) and not quoted_official):
            # official status linked from the aggregator text counts; the aggregator author does not
            if not (_official_url(urls) or any(status_handle(u) in OFFICIAL_ORG_HANDLES for u in urls)) or not _substantive(evidence):
                return {"allow": False, "reason": "aggregator_unverified", "kind": None,
                        "evidence": "source" if evidence else "missing"}
    substantive = _substantive(evidence) if evidence else False
    if evidence and not substantive:
        return {"allow": False, "reason": "not_newsworthy", "kind": None, "evidence": "source"}
    if not evidence:
        if _privileged(item) or AI_CLAIM_RE.search(claim):
            return {"allow": False, "reason": "classification_without_evidence", "kind": None, "evidence": "missing"}
        if author and author not in OFFICIAL_ORG_HANDLES and (NEWS_RE.search(claim) or _game_url(urls)):
            return {"allow": False, "reason": "source_text_required", "kind": None, "evidence": "missing"}
        return {"allow": True, "reason": None, "kind": "unchecked_legacy", "evidence": "missing"}
    officialish = author in OFFICIAL_ORG_HANDLES or _official_url(urls) or bool(item.get("official") or item.get("first_party"))
    original = not item.get("quoted") and not item.get("is_quote")
    if not officialish and not (original and substantive):
        return {"allow": False, "reason": "ungrounded_claim", "kind": None, "evidence": "source"}
    if _privileged(item) and not substantive:
        return {"allow": False, "reason": "classification_without_evidence", "kind": None, "evidence": "source"}
    kind = "useful_case" if item.get("kind") == "useful_case" or (PRACTICAL_RE.search(evidence) and not NEWS_RE.search(evidence)) else "product_update" if NEWS_RE.search(evidence) else "news"
    return {"allow": True, "reason": None, "kind": kind, "evidence": "source"}


def _content_lines(body: str) -> list[str]:
    plain = re.sub(r"<[^>]+>", "", body or "")
    plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    lines = []
    for raw in plain.splitlines():
        line = re.sub(r"[ \t]{2,}", " ", raw).strip()
        if not line or LINK_LINE_RE.match(line):
            continue
        lines.append(line)
    return lines


def _norm_line(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[^\w가-힣]+", "", (text or "").lower()))


def banned_ending(line: str) -> bool:
    t = re.sub(r"[\s.!?~…·,'\"”’)\]]+$", "", str(line or "").strip())
    if not t:
        return False
    last = t.split()[-1]
    if last in LAST_WORD_ALLOW:
        return False
    return bool(BANNED_ENDING_RE.search(t))


def prepare_korean(body: str, *, enforce: bool = True) -> dict:
    """Headline plus a distinct ~다 why-it-matters line. Emoji and bold are removed, not rewritten."""
    text = str(body or "")
    sanitized: list[str] = []
    if EMOJI_RE.search(text) or any(ch in text for ch in "🚨⚠️🛡️💰💵📑📋⛔🆕🪙🏛️📊🧾⚖️🎁💸🤖🧠✨📈📉💹📄🔎🔗👉📎💬🔥💥"):
        text = EMOJI_RE.sub("", text)
        for ch in "🚨⚠️🛡️💰💵📑📋⛔🆕🪙🏛️📊🧾⚖️🎁💸🤖🧠✨📈📉💹📄🔎🔗👉📎💬🔥💥":
            text = text.replace(ch, "")
        sanitized.append("emoji")
    if BOLD_RE.search(text):
        text = BOLD_RE.sub("", text)
        sanitized.append("bold")
    text = re.sub(r"[ \t]{2,}", " ", text)
    if not enforce or not HANGUL_RE.search(text):
        return {"body": text, "reject": False, "reasons": [], "sanitized": sanitized}
    lines = _content_lines(text)
    reasons: list[str] = []
    if len(MENTION_RE.findall(text)) >= 2:
        reasons.append("mention_list")
    for line in lines:
        if banned_ending(line):
            reasons.append("banned_ending")
            break
        if REPORT_RE.search(line):
            reasons.append("summary_report_verb")
            break
    if len(lines) < 2:
        reasons.append("missing_why_it_matters")
    else:
        head, rest = lines[0], lines[1:]
        if not any(re.search(r"다$", re.sub(r"[\s.!?…]+$", "", line)) for line in rest):
            reasons.append("missing_why_it_matters")
        if any(_norm_line(line) == _norm_line(head) for line in rest):
            reasons.append("title_repetition")
    return {"body": text, "reject": bool(reasons), "reasons": list(dict.fromkeys(reasons)), "sanitized": sanitized}


def extra_comment_issues(comment: str, headline: str = "") -> list[str]:
    """Stricter curator-line checks shared with tg_post.comment_issues."""
    cm = str(comment or "").strip()
    if not cm:
        return []
    out = []
    parts = re.split(r"(?<=[.다요])\s+|\n", cm)
    for part in parts:
        if banned_ending(part):
            out.append("comment banned ending (~임/~함/~네)")
            break
    if headline and _norm_line(cm) == _norm_line(headline):
        out.append("comment repeats the headline")
    if not re.search(r"다(?:[.!?…\s]|$)", cm) and HANGUL_RE.search(cm):
        out.append("comment missing why-it-matters (~다)")
    if EMOJI_RE.search(cm):
        out.append("comment emoji")
    if len(MENTION_RE.findall(cm)) >= 1:
        out.append("comment mention")
    return out


def _item_from_parts(body: str, meta: dict | None, photo: str | None) -> dict:
    meta = meta or {}
    item = {
        "body": body,
        "headline": meta.get("headline"),
        "url": meta.get("url"),
        "source_text": meta.get("source_text"),
        "quoted_text": meta.get("quoted_text"),
        "author": meta.get("author") or meta.get("entity"),
        "classification": meta.get("classification") or meta.get("tier"),
        "lane": meta.get("lane"),
        "ref": meta.get("ref"),
        "kind": meta.get("kind"),
        "extra_urls": meta.get("extra_urls"),
        "aggregator": meta.get("aggregator"),
        "author_role": meta.get("author_role"),
        "official": meta.get("official"),
        "first_party": meta.get("first_party"),
        "quoted": meta.get("quoted"),
        "photo": photo,
    }
    return item


def apply_send_gate(body: str, photo: str | None = None, meta: dict | None = None) -> dict:
    """Final send and legacy queue share this gate. Rejection drops the send; bad media becomes text."""
    meta = dict(meta or {})
    item = _item_from_parts(body, meta, photo)
    if meta.get("quality_checked"):
        verdict = {"allow": True, "reason": None, "kind": "checked", "evidence": "checked"}
    else:
        verdict = assess_newsworthiness(item)
    media_in = meta.get("media") if isinstance(meta.get("media"), dict) else {}
    decision = gate_photo_url(
        photo,
        provenance=media_in.get("provenance") or meta.get("media_provenance"),
        story_text=str(item.get("source_text") or "") + "\n" + str(item.get("headline") or ""),
        alt=str(media_in.get("alt") or ""),
        verified_relevant=bool(media_in.get("approved") and media_in.get("provenance") == "verified_demo"),
        origin=media_in.get("origin"),
        kind=media_in.get("kind"),
    )
    if media_in.get("rejected"):
        seen = {r.get("url") for r in decision.get("rejected") or []}
        for row in media_in["rejected"]:
            if row.get("url") not in seen:
                decision.setdefault("rejected", []).append(row)
    if not verdict["allow"]:
        return {"drop": True, "reason": verdict["reason"], "body": body, "photo": None, "no_preview": True,
                "media": decision, "verdict": verdict, "korean": None}
    korean = prepare_korean(body, enforce=str(meta.get("lane") or "") != "manual")
    if korean["reject"]:
        return {"drop": True, "reason": "korean_style:" + ",".join(korean["reasons"]), "body": korean["body"],
                "photo": None, "no_preview": True, "media": decision, "verdict": verdict, "korean": korean}
    dup = None
    if meta.get("seen_records") is not None:
        dup = duplicate_reason(story_keys(item), meta.get("seen_records"), ignore_ref=meta.get("ref"),
                               ignore_qid=meta.get("qid"))
    if dup:
        return {"drop": True, "reason": dup, "body": korean["body"], "photo": None, "no_preview": True,
                "media": decision, "verdict": verdict, "korean": korean}
    return {"drop": False, "reason": None, "body": korean["body"], "photo": decision.get("url") if decision.get("approved") else None,
            "no_preview": True, "media": decision, "verdict": verdict, "korean": korean}


def vet_queued(item: dict, seen: list[dict] | None = None) -> dict:
    meta = {
        "headline": item.get("headline"),
        "url": item.get("url"),
        "source_text": item.get("source_text"),
        "quoted_text": item.get("quoted_text"),
        "author": item.get("author") or item.get("entity"),
        "entity": item.get("entity"),
        "classification": item.get("classification") or item.get("tier"),
        "lane": item.get("lane"),
        "ref": item.get("ref"),
        "qid": item.get("qid"),
        "kind": item.get("kind"),
        "media": item.get("media"),
        "media_provenance": item.get("media_provenance"),
        "seen_records": seen,
        "extra_urls": item.get("extra_urls"),
        "aggregator": item.get("aggregator"),
        "author_role": item.get("author_role"),
        "official": item.get("official"),
        "first_party": item.get("first_party"),
    }
    return apply_send_gate(str(item.get("body") or ""), item.get("photo"), meta)


def media_log(decision: dict | None) -> dict:
    decision = decision or {}
    url = decision.get("url")
    logged_url = sanitize_source_url(url) if str(url or "").startswith("http") else (str(url) if url else None)
    rejected = []
    for row in decision.get("rejected") or []:
        ru = row.get("url")
        rejected.append({
            "url": sanitize_source_url(ru) if str(ru or "").startswith("http") else ru,
            "kind": row.get("kind"),
            "reason": row.get("reason"),
        })
    return {"url": logged_url, "provenance": decision.get("provenance"), "approved": bool(decision.get("approved")),
            "preview": False, "rejected": rejected}
