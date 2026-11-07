"""Telegram mirror post formatting + sending (2026-10-31 tgformat).

* HTML parse_mode, bold headline (+ short context like "(Day 26)"), link always last
* 3 rotating formats chosen by story size / content, never the same as the previous post (kv tg_last_format):
    flash   = headline + 1 line
    bullets = headline + 2-4 bullets ("•" / "—" alternate, kv tg_last_bullet)
    take    = headline + short summary + "💬 한줄평" (fact-grounded read)
* media: quoted post photo / video thumbnail (post data first, else free api.fxtwitter.com, no X API) -> sendPhoto
  (caption <= 1024 chars), else / on failure sendMessage with the link preview
* rules kept: facts only from the quoted post, never a "(미확인)" marker (2026-11-01), no hashtags
* 2026-11-02 tggrowth: one-line curator comment first + short bodies (tg_style), see comment_issues / ensure_comment
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.request
from pathlib import Path

from .. import db

log = logging.getLogger("yb.tgpost")

KV_LAST_FMT = "tg_last_format"
KV_LAST_BULLET = "tg_last_bullet"
KV_RENDER = "tg_render"          # item_id -> {"parse_mode", "photo", "fmt"} for ai_news.post_tg
FORMATS = ("flash", "bullets", "take")
BULLETS = ("•", "—")
CAPTION_MAX = 1024
TEXT_MAX = 4000

TG_SYSTEM = """You write a Korean Telegram news post for an AI/crypto channel from the given X sources.
The FIRST source is the quoted post: report ONLY that story; ignore other sources about a different topic.
Return JSON:
{"company": "...", "headline": "...", "context": "...", "flash": "...", "points": ["...", ...], "summary": "...", "take": "...",
 "size": "flash|normal|big"}
- company: short Korean name of the subject company/org/person (e.g. 오픈AI, 바이낸스, ICE), "" if none.
- headline: short Korean, what happened (the caller prefixes the company name; do not repeat it).
- context: "" or a very short tag that is literally in the source, e.g. "Day 26", "v2.1", "Q4" (no invented labels).
- flash: ONE short Korean line: the key fact, then why it matters to the reader (~다). Never just a filing/label restated.
- points: 2-4 short Korean factual lines (numbers, prices, dates, who is affected), each a different fact.
- summary: 1-2 short Korean sentences summarizing the facts.
- take: ONE short Korean line (한줄평) = our read of what the stated facts mean for users/traders, derived ONLY from the
  facts in the source (e.g. compare stated numbers, who gains/loses access). No predictions, rumors, hype or new facts.
- size: "flash" if the post holds one fact, "big" if it is a major official launch/price/regulatory/hack story with many
  facts, else "normal".
ONLY facts in the sources; no opinions except in take; no URLs, no hashtags, no emoji.
Never write "(미확인)", 미확인 or 추정 tags (2026-11-01 owner rule). If "verified" is false phrase it with attribution
instead, e.g. "Codex 사용량 페이지에 'GPT-6.2 Astra'가 노출됐다는 제보", "...라는 보도".
- Report WHAT WAS ANNOUNCED, never that someone posted something: never write "X가 '...'라는 글을 게시했다", "'...' 게시",
  "...라고 올렸다", and never just repeat the post text or its title. Sources labelled "context:" (same author's thread or
  earlier posts, quoted/linked pages) explain short or cryptic posts like "Banked 1/2" / "1/n"; use them when clearly
  related. If the sources do not show what was actually announced, return {"skip": true}.
- Natural Korean only (product names may stay in Latin script); no English sentences."""

# 2026-11-02 tggrowth: human-curated feel (targets.yaml tg_style; comment: false / enabled: false = old renders).
# Every TG render starts with a one-line curator comment grounded in the source; bodies stay short (body_max plain chars
# excl. the link; at most max_bullets bullets). A bad comment is regenerated once, then dropped (never blocks news).
HYPE_RE = re.compile(r"역대급|충격|대박|미쳤|🚀|필수|무조건|난리|폭등|폭락|떡상|떡락|초대박|레전드|소름|경악|"
                     r"반드시|꼭 봐|놓치지|지금 당장|\bmust\b|\bhuge\b|🔥|💥|‼|!!", re.I)
ADVICE_RE = re.compile(r"매수|매도|사야|팔아야|손절|익절|진입|롱 ?잡|숏 ?잡|추매|물타기|존버|투자 ?(?:하세요|추천|기회)|"
                       r"담아|비중 ?(?:확대|축소)|\bbuy\b|\bsell\b", re.I)
_HANGUL = re.compile(r"[가-힣]")

# 2026-11-03 tgtone2 (owner: "말투도 여전히 이상한데"): one natural Korean TG curator voice for every lane.
# headline (punchy, specific) + lead (1-2 sentences: context = who/what, then why it matters) + 0-3 concrete bullets.
STYLE_GUIDE = """
=== Korean Telegram voice (applies to headline / comment / points) ===
2026-11-03 tgstyle3 (owner: "너무 AI 티 남, 전부 고쳐"): write like a person who just saw this and drops ONE line in
their own channel. NOT a news summary, NOT a press release, NOT translated English, NOT a report about who said what.
- ONE post = ONE point. Pick the single most interesting fact; everything else stays in the link.
- headline: short + specific, max 22 chars (the company name is prefixed separately; do not repeat it). Put the
  concrete thing in it (product, number, country, coin). Good: "dots에 GPT-6.1 Terra 붙인다", "811 BTC 또 샀다",
  "3분기 매출 170억 달러". Never vague: "~확대 추진", "~관련 소식", "~발표", "~업데이트".
- comment = the line right under the headline: REQUIRED. 1-2 SHORT sentences on ONE line, max 80 chars. First: what it
  actually is. Second (required): why it matters to the reader in plain ~다 ending (e.g. "승인되면 미국 이용자도 정식으로
  쓸 수 있다", "이용자 자금 손실은 거래소가 메꾼다"). Context ONLY from "author"/"glossary"/sources. No final period after
  the whole line is fine, but each sentence should use ~다 / noun ending — never end the post on "~임"/"~함". For company
  purchases write "매입"/"사들임" (never "매수"). Keep numbers exact to the source.
- points: return [] (posts have no bullet list). Only if one extra number is truly essential: ONE item <= 30 chars.
- Endings (2026-11-05 tg-ending, owner: "~사칭임" 어색함): plain 개조식 fact ending. Prefer a noun / noun phrase
  ("~ 재개 중", "~ 사칭이니 주의", "~부터 적용", "~달러 규모"), "~다" (재개했다, 늘었다), or "~중". "~임"/"~함"/"~했음"
  at most ONCE per post and never as the last word of the post; never a judgment glued to "~임" ("사칭임", "다른 주소임").
  Vary the last word between posts. BAD: "돈 받고 출금 순서를 앞당겨 준다는 연락은 사칭임" -> GOOD: "돈 받고 출금
  순서를 앞당겨 준다는 연락은 전부 사칭이니 주의". BAD: "결제액 역대 최고치 찍었음" -> GOOD: "결제액 월간 역대 최고치".
- BANNED summary-report verbs (they make it read like an AI summary): "~를 짚었다", "~라고 설명했다", "~라고 지적했다",
  "~로 전망했다", "~라고 강조했다", "~라고 밝혔다", "~라고 언급했다", "~라고 덧붙였다", "~를 소개했다", "~라고 평가했다",
  "~라고 주장했다", "~라고 분석했다", "~로 내다봤다". Say the thing itself, not that someone said it.
- No @handles, no lists of people, no URLs, no hashtags, no emoji, no bullet characters, no bold.
- BANNED translationese: "~라는 얘기/~다는 얘기/~라는 뜻", stacked "~예정", "~작업 중", "~추진", "~를 통해", "~에 있어",
  "~의 일환으로", "~것으로 보인다", "~할 수 있게 된다", "~에 따르면"; word-by-word English ("모델 패밀리", "헤즈업",
  "프론티어 모델들"); stiff connectors ("또한", "한편", "이에 따라", "이를 통해", "아울러").
  BANNED self-talk / 존댓말: ~네, ~군, ~구나, ~느낌, ~려나, ~ㄹ까, ~인가, ~겠지, ~요, ~죠, ~습니다.
  BANNED vague: "~하는 방향", "흐름", "행보", "신호", "분위기", "움직임".
- Never invent facts, numbers or context; no hype words, no investment advice, no question to the reader.
Voice examples (invented items: copy the VOICE, never these facts):
  {"company": "구글", "headline": "Gemini 앱에 에이전트 모드 풀었다",
   "comment": "웹 예약·장보기를 대신 해주는 기능. 미국 유료 구독자부터 적용", "points": []}
  {"company": "바이낸스", "headline": "XYZ 현물 상장, 오늘 밤 거래 시작",
   "comment": "시드 태그 붙은 상장이라 변동성 큰 종목으로 분류", "points": []}
  {"company": "블랙록", "headline": "비트코인 ETF로 하루 5억 달러 들어왔다",
   "comment": "9월 이후 최대 순유입. 가격 빠지는 동안에도 돈은 계속 들어온 셈", "points": []}
  BAD (never like this): "MCP 설치 장벽 문제를 짚었다", "A는 ~라고 설명했다. B는 ~로 전망했다. C는 ~라고 지적했다",
  "dots 구동 모델 확대 추진", "모델이 늘어난다는 얘기", "• 향후 다른 모델도 추가 예정", "속도와 성격을 함께 다듬는 방향이네"."""
COMMENT_TONE = STYLE_GUIDE   # (name kept for older imports)
COMMENT_RULE = """
- comment: see the LEAD rule below; never start with an opener listed in avoid_openers, do not default to "이건"/"이번",
  not a repeat of the headline, no numbers that are not in the sources, no "(미확인)".
- Readability: flash <= 40 chars; summary ONE sentence <= 60 chars. Caveats and fine print stay out (the link has them).
""" + STYLE_GUIDE
COMMENT_FIX_SYSTEM = """You rewrite the LEAD line of a Korean Telegram news post.
Input: {"source": ..., "post": ..., "rejected_comment": ..., "rejected_because": [...]}. Return JSON {"comment": "..."}.
1-2 short casual sentences on one line, max 80 chars: what it actually is, then (optional) the one interesting bit.
Never a summary-report verb (짚었다/설명했다/지적했다/전망했다/밝혔다...), no @handle, no emoji.
Grounded ONLY in the source. If you cannot write a good one return {"comment": ""}.""" + STYLE_GUIDE


def style_guide() -> str:
    """Prompt add-on for lanes with their own JSON schema (empty when tg_style is off)."""
    return STYLE_GUIDE if style()["comment"] else ""


def style() -> dict:
    from .. import config
    c = dict(config.targets().get("tg_style") or {})
    c.setdefault("enabled", False)
    c.setdefault("comment", True)
    c.setdefault("body_max", 260)
    c.setdefault("max_bullets", 2)
    c.setdefault("comment_max", 110)
    c.setdefault("comment_style", "plain")
    c.setdefault("card", True)
    c.setdefault("fix_comment_llm", True)
    c.setdefault("layout", "classic")
    c.setdefault("headline_max", 25)
    c.setdefault("bullet_max", 30)
    # 2026-11-03 tgstyle3: plain: true -> layout "plain" (_compose_plain). A separate key so a not-yet-restarted
    # process (old code only knows classic/compact) is unaffected; rollback = plain: false.
    c.setdefault("plain", False)
    c.setdefault("plain_max_bullets", 0)
    c.setdefault("emoji", False)
    if c["plain"]:
        c["layout"] = "plain"
    if not c["enabled"]:
        c["layout"] = "classic"
        c["comment"] = False
    return c


def comment_rule() -> str:
    """Prompt add-on for every TG writer (empty when tg_style is off)."""
    return COMMENT_RULE if style()["comment"] else ""

# 2026-11-03 tgtone: self-talk endings / vague abstract endings -> comment rejected (regenerated once, else dropped)
SELFTALK_RE = re.compile(r"(?:(?<!동)(?<!아테)네|(?:[이하었았겠는던]|이었)군|구나|구먼|느낌(?:이다|임)?|려나|까|"
                         r"[인는]가|겠지|예요|에요|이요|[해어아세]요|군요|네요|죠|습니다|입니다)$")
# 2026-11-03 tgtone2: translationese endings (owner: "말투 여전히 이상")
XLATE_RE = re.compile(r"[라다]는 (?:얘기|뜻|말|것)(?:이다|임)?$|하는 방향|작업 ?중$|추진(?:한다| 중|함)?$|의 일환")
# 2026-11-03 tgstyle3 (owner: "AI 티 남"): summary-report verbs anywhere in a comment / storyline line -> rejected
REPORT_RE = re.compile(r"(?:짚었|설명했|지적했|전망했|강조했|밝혔|언급했|덧붙였|소개했|평가했|주장했|분석했|내다봤)"
                       r"(?:다|음|고|으며|는데|던)?(?=$|[\s.,!?])")
WEAK_PT_RE = re.compile(r"^향후|(?:작업 ?중|추진 ?중?|추가 예정|확대 예정|예정|계획|기대)$")
VAGUE_RE = re.compile(r"(?:방향|흐름|행보|분위기|움직임|시그널|신호탄?|모습|양상|국면)(?:이다|임|인 ?듯|같다|이네|이야)?$")


def tone_issue(cm: str) -> str | None:
    t = re.sub(r"[\s.!?~…·,'\"”’)\]]+$", "", str(cm or "").strip())
    m = SELFTALK_RE.search(t)
    if m:
        return "comment self-talk ending: ~" + m.group(0)
    m = VAGUE_RE.search(t)
    if m:
        return "comment vague ending: " + m.group(0)
    m = REPORT_RE.search(t)
    if m:
        return "comment summary-report verb: " + m.group(0)
    for part in re.split(r"(?<=[.다함임음듯셈])\s+", t):
        m = SELFTALK_RE.search(part.rstrip(". ")) or XLATE_RE.search(part.rstrip(". "))
        if m:
            return "comment awkward ending: ~" + m.group(0)
    return None


def weak_point(p: str) -> bool:
    """Bullet that carries no concrete fact ("향후 ~ 예정", "~작업 중") -> dropped by compose (2026-11-03 tgtone2)."""
    p = _clean(p)
    return bool(WEAK_PT_RE.search(p.rstrip(". "))) and not re.search(r"\d", p)


def comment_issues(cm: str, source: str = "", headline: str = "") -> list[str]:
    cm = str(cm or "").strip()
    if not cm:
        return []
    out = []
    if len(cm) > int(style()["comment_max"]):
        out.append(f"comment too long ({len(cm)} chars)")
    if "\n" in cm:
        out.append("comment must be one line")
    if HYPE_RE.search(cm):
        out.append("comment hype word: " + HYPE_RE.search(cm).group(0))
    if ADVICE_RE.search(cm):
        out.append("comment investment advice: " + ADVICE_RE.search(cm).group(0))
    if re.search(r"미확인|unverified", cm, re.I):
        out.append("comment (미확인) marker")
    if not _HANGUL.search(cm):
        out.append("comment not Korean")
    if re.search(r"https?://|#\w|@\w", cm):
        out.append("comment has link/hashtag/mention")
    if source:
        from .data_reports import ungrounded
        nums = ungrounded(cm, source)
        if nums:
            out.append("comment number not in source: " + ", ".join(nums))
    if headline and _clean(cm) == _clean(headline):
        out.append("comment repeats the headline")
    # 2026-11-06 no-weekly: lead must say why it matters (plain ~다 / consequence), not a bare restatement
    if cm and not re.search(r"(다|요)$", cm.rstrip(" .…")) and not re.search(
            r"(수 있다|된다|됐다|메꾼다|열린다|풀린다|바뀐다|줄인다|늘었다|줄었다|끊는다|끊었다|쓴다|쓴다)", cm):
        # allow noun-phrase endings already preferred by STYLE, but reject bare "~임/~함/~음" closers
        if re.search(r"(임|함|음|됨)$", cm.rstrip(" .…")):
            out.append("comment missing why-it-matters (~다)")
    ti = tone_issue(cm)
    if ti:
        out.append(ti)
    return out


def ensure_comment(f: dict, source: str = "", post: str = "") -> dict:
    """Validate f["comment"]; one LLM fix if bad/missing, then drop. Mutates + returns f. Never raises."""
    c = style()
    if not c["comment"]:
        f.pop("comment", None)
        return f
    cm = _clean(f.get("comment") or "")
    bad = comment_issues(cm, source, f.get("headline") or "") if cm else ["missing"]
    if not bad:
        f["comment"] = cm
        return f
    f.pop("comment", None)
    if not c["fix_comment_llm"]:
        return f
    try:
        from . import llm
        j = llm.chat_json("tg_comment", COMMENT_FIX_SYSTEM,
                          json.dumps({"source": source[:1500], "post": post or f.get("headline") or "",
                                      "rejected_comment": cm, "rejected_because": bad}, ensure_ascii=False),
                          max_tokens=120, temperature=0.5)
        cm2 = _clean(j.get("comment") or "")
    except Exception as e:
        log.info("comment fix failed: %s", type(e).__name__)
        return f
    if cm2 and not comment_issues(cm2, source, f.get("headline") or ""):
        f["comment"] = cm2
    return f


def split_comment_issues(bad: list[str]) -> tuple[list[str], list[str]]:
    return [b for b in bad if not b.startswith("comment")], [b for b in bad if b.startswith("comment")]


_TAG_RE = re.compile(r"<[^>]+>")
_HASHTAG_RE = re.compile(r"(?<![\w&])#(?!\d)[^\s#]+")
_MARK_RE = re.compile(r"\s*[\(\[]\s*(미확인|unverified|unconfirmed)\s*[\)\]]\s*|\s*,?\s*미확인\.?(?=\s|$)", re.I)


def _clean(s: str) -> str:
    """No hashtags, no markers, no leading bullet chars, single spaces."""
    s = _HASHTAG_RE.sub("", str(s or ""))
    s = _MARK_RE.sub(" ", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip().lstrip(":·-•—▪ ").strip()
    return re.sub(r" +([.,])", r"\1", s)


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def plain_len(body: str) -> int:
    """Length Telegram counts for a caption/text (after HTML entity parsing)."""
    return len(html.unescape(_TAG_RE.sub("", body or "")))


# ------------------------------------------------------------------ media
def _status(url: str) -> tuple[str, str] | None:
    m = re.search(r"(?:x|twitter|fxtwitter|vxtwitter|fixupx)\.com/([^/]+)/status(?:es)?/(\d+)", url or "")
    return (m.group(1), m.group(2)) if m else None


def fx_media(url: str, post: dict | None = None) -> str | None:
    """Photo URL (or video/gif thumbnail) of an X post: collected post data first, else api.fxtwitter.com (free)."""
    if post:
        try:
            from .ai_news import _media_urls_from_post
            us = _media_urls_from_post(post)
            if us:
                return us[0]
        except Exception:
            pass
    st = _status(url)
    if not st:
        return None
    try:
        req = urllib.request.Request(f"https://api.fxtwitter.com/{st[0]}/status/{st[1]}",
                                     headers={"User-Agent": "yapping-bot/1.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception as e:
        log.warning("fxtwitter media %s: %s", url, e)
        return None
    t = d.get("tweet") or {}
    med = t.get("media") or {}
    for p in med.get("photos") or []:
        if p.get("url"):
            return p["url"]
    for v in (med.get("videos") or []) + (med.get("all") or []):
        if v.get("thumbnail_url"):
            return v["thumbnail_url"]
    return None


# 2026-11-05 tweetcap-main: official-notice accounts attach brand cards ("ANNOUNCEMENT" logo art). For those,
# and for any image that looks like a flat logo/banner card, post a screenshot of the tweet itself instead.
OFFICIAL_CAPTURE = {"binance", "binance_kr", "bybit_official", "okx", "coinbase", "krakenfx", "bitget", "kucoincom",
                    "gate_io", "htx_global", "upbitglobal", "bithumbofficial"}
TWEETCAP_JS = Path(__file__).resolve().parents[2] / "tools/anncap/tweetcap.js"
TWEETCAP_DIR = Path(__file__).resolve().parents[2] / "data/tweetcap"


def logo_card(photo: str, link: str) -> bool:
    """True for official-notice brand cards and flat logo/mark images (2026-11-06: broader mark detection)."""
    st = _status(link)
    if st and st[0].lower() in OFFICIAL_CAPTURE:
        return True
    if not str(photo).startswith("http"):
        return False
    try:
        import io
        from PIL import Image, ImageStat
        raw = urllib.request.urlopen(urllib.request.Request(photo, headers={"User-Agent": "yapping-bot/1.0"}),
                                     timeout=12).read()
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.width, im.height
        if w < 300 or h < 160:
            return True
        # 2026-11-06 no-weekly: catch single-mark logos on flat/near-flat backgrounds (Hyperliquid-style)
        small = im.resize((160, max(1, int(160 * h / w))))
        q = small.quantize(colors=16)
        hist = sorted(q.histogram()[:16], reverse=True)
        total = max(1, sum(hist))
        if sum(hist[:3]) / total > 0.78:          # was 0.9; flat brand cards + simple marks
            return True
        if hist[0] / total > 0.55 and sum(hist[:2]) / total > 0.75:
            return True
        ratio = w / max(1, h)
        stv = ImageStat.Stat(small)
        if 0.75 <= ratio <= 1.35 and sum(stv.var) / 3 < 1800:
            return True
        # near-square tile whose four corners match (solid bg + centered mark)
        if 0.7 <= ratio <= 1.4:
            px = small.load()
            sw, sh = small.size
            corners = [px[0, 0], px[sw - 1, 0], px[0, sh - 1], px[sw - 1, sh - 1]]

            def _close(a, b, tol=28):
                return all(abs(int(a[i]) - int(b[i])) <= tol for i in range(3))

            if all(_close(corners[0], c) for c in corners[1:]):
                border = []
                for x in range(sw):
                    border.extend((px[x, 0], px[x, sh - 1]))
                for y in range(sh):
                    border.extend((px[0, y], px[sw - 1, y]))
                match = sum(1 for c in border if _close(corners[0], c))
                if match / max(1, len(border)) >= 0.55:
                    return True
        return False
    except Exception as e:
        log.info("logo_card check: %s", type(e).__name__)
        return False


def tweet_capture(link: str) -> str | None:
    st = _status(link)
    if not st or not TWEETCAP_JS.exists():
        return None
    import subprocess
    TWEETCAP_DIR.mkdir(parents=True, exist_ok=True)
    out = TWEETCAP_DIR / f"{st[1]}.png"
    try:
        r = subprocess.run(["node", str(TWEETCAP_JS), st[1], str(out)], capture_output=True, text=True, timeout=60,
                           cwd=str(TWEETCAP_JS.parent))
        if r.returncode == 0 and out.exists() and out.stat().st_size > 20000:
            return str(out)
    except Exception as e:
        log.info("tweet capture: %s", type(e).__name__)
    return None


# ------------------------------------------------------------------ format choice + render
def pick_format(f: dict, importance: int, prev: str | None) -> str:
    pts = [p for p in f.get("points") or [] if p]
    ok = {"flash": bool(f.get("flash")), "bullets": len(pts) >= 2, "take": bool(f.get("summary") and f.get("take"))}
    size = str(f.get("size") or "normal")
    if size == "flash" or len(pts) <= 1:
        order = ["flash", "take", "bullets"]
    elif size == "big" or importance >= 5 or len(pts) >= 4:
        order = ["bullets", "take", "flash"]
    else:
        order = ["take", "bullets", "flash"]
    for x in order:
        if ok[x] and x != prev:
            return x
    for x in order:
        if ok[x]:
            return x
    return "flash"


def headline_html(company: str, headline: str, context: str, verified: bool, underline: bool = False) -> str:
    from .ai_news import _strip_company
    h = _clean(_strip_company(company, headline))
    co = (company or "").strip()
    head = f"{co}, {h}" if co and co not in h else h
    # 2026-11-01 owner rule: never add a (미확인) marker (verified kept for call compatibility)
    ctx = _clean(context)
    if ctx and ctx.lower() not in head.lower():
        head += f" ({ctx.strip('()')})"
    inner = f"<u>{esc(head)}</u>" if underline else esc(head)
    return f"<b>{inner}</b>"


def render(f: dict, fmt: str, company: str, verified: bool, link: str, bullet: str = "•") -> str:
    st = style()
    head = headline_html(company, f.get("headline") or "", f.get("context") or "", verified,
                         underline=(fmt == "bullets" and f.get("size") == "big"))
    cm = _clean(f.get("comment") or "") if st["comment"] else ""
    if st.get("layout") in ("compact", "plain"):   # 2026-11-02 tgstyle (+ 2026-11-03 tgstyle3 plain)
        head = headline_html(company, f.get("headline") or "", f.get("context") or "", verified)
        pts = [p for p in f.get("points") or [] if _clean(p)]
        if cm:   # 2026-11-03 tgtone2: the lead carries the story; bullets = extra concrete facts only (may be none)
            return compose(plain_text(head), cm, pts, link, head_html=head, bullet=bullet)
        if fmt == "flash" or not pts:
            pts = [x for x in (f.get("flash"), f.get("summary")) if _clean(x)][:1] + ([] if fmt == "flash" else pts)
        if fmt == "take" and _clean(f.get("summary")) and not cm:
            cm = _clean(f.get("take") or "")
        return compose(plain_text(head), cm, pts, link, head_html=head, bullet=bullet)
    if fmt == "flash":
        body = f"{head}\n{esc(_clean(f.get('flash')))}"
    elif fmt == "bullets":
        pts = [_clean(p) for p in f.get("points") or [] if _clean(p)][:(int(st["max_bullets"]) if st["enabled"] else 4)]
        body = head + "\n\n" + "\n".join(f"{bullet} {esc(p)}" for p in pts)
    elif cm:   # 2026-11-02 tggrowth: the curator comment replaces the 한줄평 line
        body = f"{head}\n{esc(_clean(f.get('summary')))}"
    else:
        body = f"{head}\n\n{esc(_clean(f.get('summary')))}\n\n💬 한줄평: {esc(_clean(f.get('take')))}"
    if cm:
        body = f"{esc(cm)}\n\n{body}"
    return f"{body}\n\n{esc(link)}" if link else body


def body_len(body: str, link: str = "") -> int:
    """Plain length excluding the trailing link (tg_style.body_max budget)."""
    b = body or ""
    if link and b.rstrip().endswith(esc(link)):
        b = b.rstrip()[: -len(esc(link))]
    return plain_len(b.strip())


def draft_fields(s: dict, srcs: list[dict], note: dict | None = None) -> dict:
    from . import llm
    pl = {"verified": bool(s.get("verified")), "sources": srcs, "avoid_openers": recent_openers()}
    try:   # 2026-11-03 tgtone2: who said it + what unfamiliar terms are (lead context)
        from . import tg_card
        au = tg_card.author_info(s.get("quote_url") or "")
        if au:
            pl["author"] = au
    except Exception:
        pass
    g = glossary(" ".join(str(x.get("text") or "") + " " + str(x.get("from") or "") for x in srcs))
    if g:
        pl["glossary"] = g
    if note:
        pl.update(note)
    from .sourcing import PRIMARY_RULE
    return llm.chat_json("breaking_tg", TG_SYSTEM + comment_rule() + PRIMARY_RULE, json.dumps(pl, ensure_ascii=False), max_tokens=900,
                         temperature=0.5 if note else 0.4)


def glossary(text: str) -> dict:
    """targets.yaml tg_glossary entries whose term appears in text (case-insensitive, word-ish match)."""
    from .. import config
    g = config.targets().get("tg_glossary") or {}
    out = {}
    for k, v in g.items():
        if re.search(r"(?<![\w])" + re.escape(str(k)) + r"(?![\w])", text or "", re.I if str(k).startswith("@") else 0):
            out[str(k)] = str(v)
    return dict(list(out.items())[:8])


def field_issues(f: dict, source: str = "") -> list[str]:
    """2026-11-01 qualityfix validator over every TG field (headline + flash/points/summary/take)."""
    from . import quality
    if f.get("skip"):
        return ["llm skip"]
    out = []
    for k in ("headline", "flash", "summary", "take"):
        v = _clean(f.get(k) or "")
        if v:
            out += [f"{k}: {i}" for i in quality.validate(v, "ko", source)]
    for p in f.get("points") or []:
        if _clean(p):
            out += [f"points: {i}" for i in quality.validate(_clean(p), "ko", source)]
    if not _clean(f.get("headline") or ""):
        out.append("headline: empty")
    if style()["comment"] and _clean(f.get("comment") or ""):   # 2026-11-02 tggrowth (see split_comment_issues)
        out += comment_issues(_clean(f.get("comment") or ""), source, f.get("headline") or "")
    return list(dict.fromkeys(out))


def build(fields: dict, company: str, verified: bool, link: str, importance: int = 4,
          prev_fmt: str | None = None, prev_bullet: str | None = None, quote_post: dict | None = None,
          fetch_media: bool = True) -> dict:
    """fields (LLM JSON) -> {"body", "fmt", "bullet", "photo"}; prev_* default to the kv state."""
    if prev_fmt is None:
        prev_fmt = db.kv_get(KV_LAST_FMT)
    if prev_bullet is None:
        prev_bullet = db.kv_get(KV_LAST_BULLET)
    fmt = pick_format(fields, importance, prev_fmt)
    bullet = BULLETS[1] if prev_bullet == BULLETS[0] else BULLETS[0]
    body = render(fields, fmt, company, verified, link, bullet)
    st = style()
    if st["enabled"] and body_len(body, link) > int(st["body_max"]):
        # 2026-11-02 tggrowth: over the budget -> prefer flash/take, else the shortest available format
        alts = []
        for x in ("flash", "take", "bullets"):
            ok = {"flash": bool(fields.get("flash")), "take": bool(fields.get("summary")),
                  "bullets": len([p for p in fields.get("points") or [] if p]) >= 2}[x]
            if ok:
                b = render(fields, x, company, verified, link, bullet)
                alts.append((body_len(b, link) > int(st["body_max"]), x == prev_fmt, body_len(b, link), x, b))
        if alts:
            alts.sort()
            _, _, _, fmt, body = alts[0]
    photo = fx_media(link, quote_post) if fetch_media else None
    logo = False
    if photo and fetch_media and logo_card(photo, link):   # 2026-11-05 tweetcap-main / 2026-11-06 broader
        logo = True
        photo = tweet_capture(link) or None   # capture fail -> text only (no logo, no fake card)
    if not photo and not logo and fetch_media and st["enabled"] and st.get("card", True):
        try:   # 2026-11-03 tgtone2: text-only source post -> rendered tweet card image
            from . import tg_card
            photo = tg_card.card(link)
        except Exception as e:
            log.info("tweet card: %s", type(e).__name__)
    return {"body": body, "fmt": fmt, "bullet": bullet if fmt == "bullets" else None, "photo": photo,
            "photo_ok": bool(photo)}


def remember(fmt: str, bullet: str | None = None) -> None:
    db.kv_set(KV_LAST_FMT, fmt)
    if bullet:
        db.kv_set(KV_LAST_BULLET, bullet)


def set_render(item_id: int, photo: str | None, fmt: str) -> None:
    r = db.kv_get(KV_RENDER, {}) or {}
    r[str(item_id)] = {"parse_mode": "HTML", "photo": photo, "fmt": fmt}
    db.kv_set(KV_RENDER, dict(list(r.items())[-300:]))


def get_render(item_id: int) -> dict:
    return (db.kv_get(KV_RENDER, {}) or {}).get(str(item_id)) or {}


# ------------------------------------------------------------------ targets
# 2026-10-31 devgroup: tg_extra_channels entries may be "@chan" or {chat, thread, name}. Thread targets are
# normalized to the string key "<chat>#<thread>" (used for dedupe kv keys); send() splits it into message_thread_id.
def target_key(x) -> str:
    if isinstance(x, dict):
        ch = str(x.get("chat") or "").strip()
        th = x.get("thread")
        return f"{ch}#{int(th)}" if ch and th not in (None, "") else ch
    return str(x or "").strip()


def split_target(key: str) -> tuple[str, int | None]:
    k = str(key)
    if "#" in k:
        ch, th = k.rsplit("#", 1)
        return ch, int(th)
    return k, None


def extra_targets() -> list[str]:
    from .. import config
    xs = (config.targets().get("ai_news") or {}).get("tg_extra_channels") or []
    return [k for k in dict.fromkeys(target_key(x) for x in xs) if k]


def send_params(chat: str) -> dict:
    ch, th = split_target(chat)
    d = {"chat_id": ch}
    if th is not None:
        d["message_thread_id"] = th
    return d


# ------------------------------------------------------------------ send
def send(token: str, chat: str, body: str, parse_mode: str | None = None, photo: str | None = None,
         quote: dict | None = None, preview: bool = True) -> dict:
    """sendPhoto (caption <= 1024) when a photo is given, else / on failure sendMessage with link preview.
    photo may be a URL or a local file path. Returns the Telegram JSON (+ "via")."""
    import requests
    base = f"https://api.telegram.org/bot{token}"
    split = bool(photo) and plain_len(body) > CAPTION_MAX   # 2026-11-03 tgtone2: photo, then the text message
    if photo:
        try:
            data = dict(send_params(chat), caption=body) if not split else dict(send_params(chat))
            if parse_mode:
                data["parse_mode"] = parse_mode
            if not str(photo).startswith("http") and Path(photo).exists():
                with open(photo, "rb") as fh:
                    d = requests.post(f"{base}/sendPhoto", data=data, files={"photo": fh}, timeout=40).json()
            else:
                d = requests.post(f"{base}/sendPhoto", json=dict(data, photo=photo), timeout=30).json()
            if d.get("ok") and not split:
                d["via"] = "photo"
                return d
            if d.get("ok"):
                pid = (d.get("result") or {}).get("message_id")
                d2 = send(token, chat, body, parse_mode, preview=preview)
                d2["via"] = "photo+" + str(d2.get("via"))
                d2["photo_message_id"] = pid
                return d2
            log.warning("sendPhoto failed (%s): %s -> text fallback", chat, str(d.get("description"))[:160])
        except Exception as e:
            log.warning("sendPhoto error (%s): %s -> text fallback", chat, type(e).__name__)
    if quote and not photo and (quote.get("chat") and quote.get("message_id") or quote.get("url")):
        dq = send_quoted(base, chat, body, parse_mode, quote)
        if dq.get("ok"):
            return dq
    payload = dict(send_params(chat), text=body, disable_web_page_preview=not preview)   # 2026-11-05 anncap
    if parse_mode:
        payload["parse_mode"] = parse_mode
    d = requests.post(f"{base}/sendMessage", json=payload, timeout=20).json()
    d["via"] = "text"
    if not d.get("ok") and parse_mode and re.search(r"parse|entit|tag", str(d.get("description") or ""), re.I):
        # 2026-11-02 tgstyle: never lose a post to a markup error -> plain text (links kept as "label: url")
        log.warning("send parse error (%s): %s -> plain text", chat, str(d.get("description"))[:160])
        payload = dict(send_params(chat), text=plain_text(body, keep_links=True), disable_web_page_preview=not preview)
        d = requests.post(f"{base}/sendMessage", json=payload, timeout=20).json()
        d["via"] = "text-plain"
    return d



def send_quoted(base: str, chat: str, body: str, parse_mode: str | None, q: dict) -> dict:
    """2026-11-03 tgquote (owner: "인용해서 생각 재미나게"): our take posted as a Bot API *external reply* to the source
    channel post (reply_parameters.chat_id = @source): Telegram shows the source channel + quoted text above our text,
    tap -> original post. Tries with an exact quote snippet, then without; then the source post as a link preview
    shown above our text. q = {"chat": "@src", "message_id": int, "url": "https://t.me/src/N", "quote": "..."}.
    Returns Telegram JSON (+ "via"); not ok -> caller falls back to the plain sendMessage."""
    import requests
    p0 = dict(send_params(chat), text=body)
    if parse_mode:
        p0["parse_mode"] = parse_mode
    d = {"ok": False, "description": "no quote target"}
    if q.get("chat") and q.get("message_id"):
        rp = {"chat_id": q["chat"], "message_id": int(q["message_id"]), "allow_sending_without_reply": False}
        tries = []
        qt = str(q.get("quote") or "").strip()
        if qt:
            tries.append(("quote", dict(rp, quote=qt[:1024])))
        tries.append(("reply", rp))
        for via, r in tries:
            d = requests.post(f"{base}/sendMessage", json=dict(p0, reply_parameters=r,
                                                                link_preview_options={"is_disabled": True}),
                              timeout=20).json()
            if d.get("ok"):
                d["via"] = "ext-" + via
                return d
            log.warning("quoted send (%s, %s) failed: %s", chat, via, str(d.get("description"))[:160])
            if via == "quote" and not re.search(r"quote", str(d.get("description") or ""), re.I):
                break   # not a quote-text mismatch -> the plain external reply would fail the same way
    if q.get("url"):
        d = requests.post(f"{base}/sendMessage", json=dict(p0, link_preview_options={
            "url": q["url"], "show_above_text": True, "prefer_small_media": True}), timeout=20).json()
        if d.get("ok"):
            d["via"] = "preview"
            return d
        log.warning("preview send (%s) failed: %s", chat, str(d.get("description"))[:160])
    return d


# ------------------------------------------------------------------ 2026-11-02 tgstyle: compact human-like layout
#   🧠 <b>headline</b>            (topic emoji, rotated; 🇯🇵 added for Japan stories)
#   <i>comment</i> | <blockquote>comment</blockquote>   (alternating)
#
#   • fact with the <b>key number</b> bold      (<= 3 bullets, <= bullet_max chars)
#
#   🔗 <a href="...">@author 원문</a>           (🔗 / 👉 / 📎 rotated)
KV_EMOJI = "tg_last_emoji"
KV_CSTYLE = "tg_last_comment_style"
KV_OPENERS = "tg_recent_openers"
EMOJI_SETS = [
    (re.compile(r"해킹|탈취|익스플로잇|exploit|hack|보안 ?사고|출금 ?(?:중단|재개|정지)|입출금|suspend|halt", re.I), ["🚨", "⚠️", "🛡️"]),
    (re.compile(r"IPO|공모|증권신고|S-1|상장 ?신청|투자 ?유치|펀딩|시리즈 ?[A-F]|밸류|valuation|raise", re.I), ["💰", "💵", "📑"]),
    (re.compile(r"상장 ?폐지|상폐|delist", re.I), ["📋", "⛔"]),
    (re.compile(r"신규 ?상장|상장한다|상장 예정|listing|will list|런치풀|launchpool|에어드랍|airdrop", re.I), ["🆕", "🪙"]),
    (re.compile(r"연준|FOMC|금리|CPI|PCE|고용|실업|GDP|ECB|BOJ|일본은행|물가|국채|달러 ?인덱스|macro", re.I), ["🏛️", "📊", "🧾"]),
    (re.compile(r"규제|SEC|CFTC|금융청|법안|라이선스|license|소송|당국|정책|약관|policy", re.I), ["⚖️", "🏛️"]),
    (re.compile(r"캠페인|리워드|보상|바우처|리베이트|이자|APR|APY|프로모션", re.I), ["🎁", "💸"]),
    (re.compile(r"에이전트|agent|Claude Code|Codex|Cursor|코딩|Devin|MCP|SDK|API|개발자", re.I), ["🤖", "🧑‍💻", "🛠️"]),
    (re.compile(r"모델|model|GPT|Claude|Gemini|Grok|Llama|벤치마크|추론", re.I), ["🧠", "✨", "🤖"]),
    (re.compile(r"ETF|급등|급락|유입|유출|가격|거래량|시총|청산", re.I), ["📈", "📉", "💹"]),
    (re.compile(r"비트코인|이더리움|BTC|ETH|솔라나|바이낸스|코인|crypto|스테이블", re.I), ["🪙", "💱", "🏦"]),
    (re.compile(r"리포트|보고서|논문|paper|research", re.I), ["📄", "🔎"]),
]
TOPIC_EMOJI = {"macro": ["🏛️", "📊", "🧾"], "report": ["📄", "🔎", "🧠"], "crypto": ["🪙", "💱", "🏦"]}
JAPAN_RE = re.compile(r"일본|Japan|EDINET|금융청|엔화|도쿄", re.I)
LINK_EMOJI = ["🔗", "👉", "📎"]
_NUM_RE = re.compile(r"(?:[$₩¥€]\s?)?\d[\d,.]*(?:\s?(?:%p|%|bp|조|억|만|천)(?:\s?(?:달러|원|엔|개|명|건|USDC|USDT|BTC|ETH|BNB))?|"
                     r"\s?(?:달러|원|엔|USDC|USDT|BTC|ETH|BNB|배|위))?")


def _rot(kv: str, opts: list[str]) -> str:
    last = db.kv_get(kv, "") or ""
    pick = next((o for o in opts if o != last), opts[0]) if len(opts) > 1 and last in opts else opts[0]
    if last in opts and len(opts) > 1:
        pick = opts[(opts.index(last) + 1) % len(opts)]
    db.kv_set(kv, pick)
    return pick


def pick_emoji(text: str, topic: str = "") -> tuple[str, str]:
    """(headline emoji, extra flag or "") chosen by topic keywords, rotated so consecutive posts differ."""
    opts = TOPIC_EMOJI.get(topic) or next((o for rx, o in EMOJI_SETS if rx.search(text or "")), ["📰", "🗞️", "📌"])
    em = _rot(KV_EMOJI, opts)
    flag = "🇯🇵" if JAPAN_RE.search(text or "") and em != "🇯🇵" else ""
    return em, flag


def plain_text(body: str, keep_links: bool = False) -> str:
    b = body or ""
    if keep_links:
        b = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', lambda m: f"{m.group(2)}: {m.group(1)}", b)
    return html.unescape(_TAG_RE.sub("", b))


def _emph(escaped: str) -> str:
    """Bold the first meaningful number of a bullet (already HTML-escaped text; numbers never contain entities)."""
    for m in _NUM_RE.finditer(escaped):
        tok = m.group(0).strip()
        if len(re.sub(r"\D", "", tok)) == 0 or re.fullmatch(r"\d{1,2}", tok) or re.fullmatch(r"20\d\d", tok):
            continue
        a, b = m.start(), m.start() + len(m.group(0).rstrip())
        if "&" in escaped[max(0, a - 6):a]:   # inside an entity like &#39;
            continue
        return escaped[:a] + f"<b>{escaped[a:b]}</b>" + escaped[b:]
    return escaped


def _fit_points(pts: list[str], bm: int, mp: int) -> list[str]:
    out = []
    for p in pts:
        if len(p) > bm + 8:   # cut at a natural break, else skip (details live in the source link)
            cut = max(p.rfind(x, 0, bm + 1) for x in (",", "·", "(", " - ", "—"))
            p = p[:cut].rstrip(" ,·(—-") if cut >= 12 else ""
        if p and p not in out:
            out.append(p)
    if not out and pts:
        p = pts[0]
        sp = p.rfind(" ", 0, bm)
        out = [(p[:sp] if sp > 10 else p[:bm]).rstrip(" ,·") + "…"]
    return out[:mp]


def _comment_html(cm: str) -> str:
    if style().get("comment_style", "plain") == "plain":   # 2026-11-03 tgtone2 (rollback: comment_style: rotate)
        return esc(cm)
    k = _rot(KV_CSTYLE, ["i", "blockquote"])
    return f"<{k}>{esc(cm)}</{k}>"


def link_line(link: str, label: str = "") -> str:
    if style().get("layout") == "plain":   # 2026-11-03 tgstyle3: one quiet link line, no emoji / no @author label
        return f'<a href="{html.escape(link, quote=True)}">{esc(label or "원문")}</a>'
    if not label:
        st = _status(link)
        label = f"@{st[0]} 원문" if st else "원문 보기"
    le = _rot("tg_last_link_emoji", LINK_EMOJI)
    return f'{le} <a href="{html.escape(link, quote=True)}">{esc(label)}</a>'


def recent_openers() -> list[str]:
    return list(db.kv_get(KV_OPENERS, []) or [])[-6:]


def _note_opener(cm: str) -> None:
    w = (cm or "").split()
    if not w:
        return
    xs = [x for x in recent_openers() if x != w[0]] + [w[0]]
    db.kv_set(KV_OPENERS, xs[-8:])


def compose(headline: str, comment: str = "", points=(), link: str = "", *, link_label: str = "", topic: str = "",
            head_html: str | None = None, bullet: str | None = None, max_points: int | None = None,
            emoji: str | None = None) -> str:
    """Compact layout used by every TG lane (see the block comment above). All text is HTML-escaped here."""
    st = style()
    if st.get("layout") == "plain":
        return _compose_plain(headline, comment, points, link, link_label=link_label, head_html=head_html,
                              max_points=max_points, emoji=emoji)
    pts = _fit_points([_clean(p) for p in points or [] if _clean(p) and not weak_point(p)], int(st["bullet_max"]),
                      int(max_points or st["max_bullets"]))
    head_txt = _clean(headline)
    em, flag = (emoji, "") if emoji else pick_emoji(" ".join([head_txt] + pts), topic)
    head = head_html or f"<b>{esc(head_txt)}</b>"
    out = f"{em} {head}" + (f" {flag}" if flag else "")
    cm = re.sub(r"(?<![.])[.]$", "", _clean(comment))   # 2026-11-03 tgtone2: TG voice drops the final period
    if cm:
        out += "\n" + _comment_html(cm)
        _note_opener(cm)
    if cm:   # 2026-11-03 tgtone2: a bullet whose key number is already in the lead adds nothing
        cd = re.sub(r"[,\s]", "", cm)
        pts = [p for p in pts if not any(len(n) >= 3 and n in cd for n in re.findall(r"\d[\d.]*", re.sub(r",", "", p)))]
    if pts:
        b = bullet or "•"
        out += "\n\n" + "\n".join(f"{b} {_emph(esc(p))}" for p in pts)
    if link:
        out += "\n\n" + link_line(link, link_label)
    return out


def _compose_plain(headline: str, comment: str = "", points=(), link: str = "", *, link_label: str = "",
                   head_html: str | None = None, max_points: int | None = None, emoji: str | None = None) -> str:
    """2026-11-03 tgstyle3 (owner: "너무 AI 티 남, 전부 고쳐"): person-written look for every TG lane.
        headline                (plain text: no topic emoji, no bold; an explicit emoji arg (e.g. 🚨 security) kept)
        one casual comment line (if missing: the first fact line instead)
        [<= max_bullets extra fact lines, no bullet chars; tg_style.max_bullets 0 = none]

        <a href=link>원문</a>    (one quiet link line, last)
    Rollback: targets.yaml tg_style.plain: false (back to the compact layout)."""
    st = style()
    head_txt = _clean(plain_text(head_html) if head_html else headline)
    cand = [_clean(p) for p in points or [] if _clean(p) and not weak_point(p)]
    cm = re.sub(r"(?<![.])[.]$", "", _clean(comment))
    if not cm and cand:
        cm = re.sub(r"(?<![.])[.]$", "", cand.pop(0))
    elif cm:
        _note_opener(cm)
        cd = re.sub(r"[,\s]", "", cm)
        cand = [p for p in cand if not any(len(n) >= 3 and n in cd
                                           for n in re.findall(r"\d[\d.]*", re.sub(r",", "", p)))]
    mp = int(st["plain_max_bullets"]) if max_points is None else min(int(max_points), int(st["plain_max_bullets"]))
    pts = _fit_points(cand, int(st["bullet_max"]), mp) if mp > 0 else []
    em = emoji or ("" if not st.get("emoji") else pick_emoji(head_txt)[0])
    out = (f"{em} " if em else "") + esc(head_txt)
    if cm:
        out += "\n" + esc(cm)
    if pts:
        out += "\n" + "\n".join(esc(p) for p in pts)
    if link:
        out += "\n\n" + link_line(link, link_label)
    return out
