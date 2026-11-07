"""Patched production services: final send and legacy queue fail closed, offline."""
from __future__ import annotations

import importlib
import shutil
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUSE_THUMB = "https://pbs.twimg.com/amplify_video_thumb/2118400683942477824/img/8Nq_kxkL9PZTDlo7.jpg"
MUSE_TEXT = (
    "Introducing Voice. Talk to your Muse and Muse talks back, so you can go from "
    "what should I cook to cooking, just like that"
)
KO_OK = "뮤즈 보이스를 열었다\n말로 물어보면 요리 순서까지 이어서 알려준다\n\n<a href=\"https://x.com/Muse/status/2118411960337289583\">원문</a>"
KST = timezone(timedelta(hours=9))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_live(tmp_path: Path):
    pkg = tmp_path / "livekit"
    live = pkg / "yb_live"
    services = live / "services"
    quality = live / "quality"
    services.mkdir(parents=True)
    quality.mkdir(parents=True)
    for src in (ROOT / "app" / "quality").glob("*.py"):
        shutil.copy(src, quality / src.name)
    for name in ("tg_post.py", "tg_route.py", "tg_storyline.py", "tg_timing.py"):
        shutil.copy(ROOT / "deploy" / "patched" / name, services / name)
    _write(live / "__init__.py", "")
    _write(services / "__init__.py", "")
    _write(live / "db.py", """
KV = {}

def kv_get(k, default=None):
    return KV.get(k, default if default is not None else None)

def kv_set(k, v):
    KV[k] = v

def iso(dt):
    return dt.isoformat()

def now_kst():
    from datetime import datetime, timedelta, timezone
    return datetime(2026, 11, 7, 17, 0, tzinfo=timezone(timedelta(hours=9)))

def kst_day(dt=None):
    return "2026-11-07"

def q(*_a, **_k):
    return []

def one(*_a, **_k):
    return {}
""")
    _write(live / "config.py", """
def targets():
    return {
        "tg_style": {"enabled": True, "comment": True, "plain": True, "layout": "plain", "body_max": 400,
                     "max_bullets": 0, "comment_max": 110, "headline_max": 40, "bullet_max": 40,
                     "plain_max_bullets": 0, "emoji": False, "card": False, "fix_comment_llm": False},
        "tg_timing": {"enabled": False, "per_flush": 1},
        "tg_route": {"enabled": True, "llm_fallback": False, "log_keep": 50,
                     "channels": {"agent": "@cblaboratory", "crypto": "@blockschool", "devgroup": "-1009#3"},
                     "devgroup_lanes": []},
        "ai_news": {"tg_extra_channels": []},
    }
""")
    _write(live / "accounts.py", """
def get(key, default=None):
    data = {
        "telegram.self_handles": [],
        "telegram.map": {},
        "telegram.devgroup_lanes": [],
        "telegram.quiet": {},
        "telegram.agent_channel_selector": "@cblaboratory",
    }
    return data.get(key, default)

def tg_channels():
    return {"agent": "@cblaboratory", "crypto": "@blockschool", "devgroup": "-1009#3"}
""")
    _write(services / "tg_curate.py", """
def check(*_a, **_k):
    return None

def filter_targets(_lane, targets, _now, **_k):
    return list(targets), {}
""")
    _write(services / "tg_forward.py", """
import re
AD_RE = re.compile(r"airdrop")
KV_LOG = "tg_forward_log"

def mark_sent(*_a, **_k):
    return None

def target_alias(tp):
    return tp
""")
    _write(services / "ai_news.py", """
def tg_channel():
    return "@cblaboratory"

def _bot_token():
    return "test-token"

def _strip_company(_company, headline):
    return headline

def post_tg(*_a, **_k):
    raise AssertionError("ai_news.post_tg must not run in offline tests")
""")
    _write(services / "data_reports.py", """
def ungrounded(*_a, **_k):
    return []
""")
    sys.path.insert(0, str(pkg))
    for name in list(sys.modules):
        if name == "yb_live" or name.startswith("yb_live."):
            del sys.modules[name]
    tg_post = importlib.import_module("yb_live.services.tg_post")
    tg_route = importlib.import_module("yb_live.services.tg_route")
    tg_timing = importlib.import_module("yb_live.services.tg_timing")
    db = importlib.import_module("yb_live.db")
    return tg_post, tg_route, tg_timing, db


def _install_requests():
    calls = []

    class Fake:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def post(url, json=None, data=None, files=None, timeout=None):
        calls.append({"url": url, "json": json, "data": data, "files": files})
        return Fake({"ok": True, "result": {"message_id": 11}})

    mod = types.ModuleType("requests")
    mod.post = post
    sys.modules["requests"] = mod
    return calls


def test_final_send_strips_video_thumb_and_disables_preview(tmp_path):
    tg_post, _route, _timing, _db = load_live(tmp_path)
    calls = _install_requests()
    result = tg_post.send(
        "TOKEN", "@cblaboratory", KO_OK, "HTML", MUSE_THUMB, preview=True,
        quality_item={
            "lane": "breaking",
            "source_text": MUSE_TEXT,
            "author": "muse",
            "url": "https://x.com/Muse/status/2118411960337289583",
        },
    )
    assert result["ok"] is True
    assert result["via"] == "text"
    assert calls and calls[0]["url"].endswith("/sendMessage")
    assert "sendPhoto" not in calls[0]["url"]
    assert calls[0]["json"]["disable_web_page_preview"] is True
    assert "TOKEN" not in calls[0]["json"]["text"]
    assert "TOKEN" not in str(result["media"])
    rejected = result["media"]["rejected"]
    assert rejected and rejected[0]["reason"] == "unchecked_video_thumbnail"
    assert MUSE_THUMB in rejected[0]["url"]


def test_joke_does_not_send(tmp_path):
    tg_post, _route, _timing, _db = load_live(tmp_path)
    calls = _install_requests()
    result = tg_post.send(
        "TOKEN", "@cblaboratory",
        "오픈AI가 새 게임을 출시했다\n에이전트가 만든 게임이라 바로 써볼 수 있다",
        "HTML", None,
        quality_item={
            "lane": "breaking",
            "source_text": "Get me out of the basement\nhttps://poteto.com/basement",
            "quoted_text": "It's Friday",
            "author": "thsottiaux",
            "classification": "openai:update/agent/top_tier",
            "url": "https://x.com/thsottiaux/status/2118938956301779171",
            "followers": 1000000,
            "employer": "OpenAI",
        },
    )
    assert result["ok"] is False
    assert "not_newsworthy_trivial" in result["description"]
    assert calls == []


def test_legacy_queue_strips_thumb_and_drops_joke(tmp_path):
    _post, tg_route, tg_timing, db = load_live(tmp_path)
    calls = _install_requests()
    sent = []

    def _deliver_one(lane, target, body, parse_mode, photo, meta):
        sent.append({"lane": lane, "photo": photo, "no_preview": meta.get("no_preview"), "body": body,
                     "media": meta.get("media")})
        return {"ok": True, "message_id": "4", "via": "text"}

    tg_route._deliver_one = _deliver_one
    later = (datetime(2026, 11, 8, 12, 0, tzinfo=KST)).isoformat()
    db.kv_set("tg_queue", [
        {"qid": "voice", "lane": "breaking", "target": "@cblaboratory", "body": KO_OK, "photo": MUSE_THUMB,
         "ref": "muse-voice", "expires_at": later, "queued_at": "2026-11-07T10:00:00+09:00",
         "source_text": MUSE_TEXT, "author": "muse", "url": "https://x.com/Muse/status/2118411960337289583"},
        {"qid": "joke", "lane": "breaking", "target": "@cblaboratory", "body": "오픈AI 게임 출시\n바로 해본다",
         "photo": "https://pbs.twimg.com/media/game.jpg", "ref": "basement", "expires_at": later,
         "queued_at": "2026-11-07T10:05:00+09:00",
         "source_text": "Get me out of the basement https://poteto.com/basement",
         "quoted_text": "It's Friday", "author": "thsottiaux",
         "classification": "openai:update/agent/top_tier",
         "url": "https://x.com/thsottiaux/status/2118938956301779171"},
    ])
    db.kv_set("tg_sent_log", [])
    out = tg_timing.flush(dry_run=False)
    assert len(sent) == 1
    assert sent[0]["photo"] in (None, "")
    assert sent[0]["no_preview"] is True
    assert any(row[2] == "not_newsworthy_trivial" or "not_newsworthy_trivial" in str(row[2]) for row in out["dropped"])
    assert calls == []
    reasons = [row[2] for row in out["dropped"]]
    assert any("trivial" in str(reason) for reason in reasons)


def test_storyline_source_does_not_fall_back_to_quote_media():
    text = (ROOT / "deploy" / "patched" / "tg_storyline.py").read_text(encoding="utf-8")
    assert "fx_media(sp[\"raw\"][\"quote\"]" not in text
    post = (ROOT / "deploy" / "patched" / "tg_post.py").read_text(encoding="utf-8")
    assert "tg_card.card" not in post
