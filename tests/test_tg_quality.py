"""Offline gates: newsworthiness, grounding, dedup, Korean, and media fail-closed."""
from __future__ import annotations

import json

import pytest

from app.quality import tg_news
from app.quality.tg_news import (
    assess_newsworthiness,
    choose_media,
    duplicate_reason,
    gate_photo_url,
    prepare_korean,
    story_keys,
)

MUSE_THUMB = "https://pbs.twimg.com/amplify_video_thumb/2118400683942477824/img/8Nq_kxkL9PZTDlo7.jpg"
MUSE_TEXT = (
    "Introducing Voice. Talk to your Muse and Muse talks back, so you can go from "
    "what should I cook to cooking, just like that"
)
MUSE_URL = "https://x.com/Muse/status/2118411960337289583"
BASEMENT_TEXT = "Get me out of the basement\n\nhttps://poteto.com/basement"
BASEMENT_URL = "https://x.com/thsottiaux/status/2118938956301779171"
KO_OK = "뮤즈 보이스를 열었다\n말로 물어보면 요리 순서까지 이어서 알려준다"


def test_muse_video_thumbnail_is_rejected_and_url_kept():
    tweet = {"media": {"photos": [], "videos": [{
        "type": "video",
        "thumbnail_url": MUSE_THUMB,
        "url": "https://video.twimg.com/amplify_video/2118400683942477824/vid.mp4",
    }]}}
    decision = choose_media(tg_news.candidates_from_fx(tweet), story_text=MUSE_TEXT, link=MUSE_URL)
    assert decision["approved"] is False
    assert decision["url"] is None
    assert decision["preview"] is False
    assert decision["allow_official_capture"] is False
    assert decision["rejected"][0]["reason"] == "unchecked_video_thumbnail"
    assert decision["rejected"][0]["url"] == MUSE_THUMB


def test_thumbnail_misfiled_as_photo_still_rejected():
    tweet = {"media": {"photos": [{"url": MUSE_THUMB, "alt_text": "Muse Voice cooking"}]}}
    decision = choose_media(tg_news.candidates_from_fx(tweet), story_text=MUSE_TEXT, link=MUSE_URL)
    assert decision["url"] is None
    assert decision["rejected"][0]["reason"] == "unchecked_video_thumbnail"


def test_quoted_photo_is_not_a_fallback():
    tweet = {"media": {}, "quote": {"media": {"photos": [{"url": "https://pbs.twimg.com/media/quote.jpg", "alt_text": "Voice"}]}}}
    decision = choose_media(tg_news.candidates_from_fx(tweet), story_text=MUSE_TEXT, link=MUSE_URL)
    assert decision["approved"] is False
    assert decision["rejected"][0]["reason"] == "quoted_post_image"
    assert decision["allow_official_capture"] is False


def test_verified_demo_kept_and_quote_recorded():
    tweet = {"media": {"photos": [{
        "url": "https://pbs.twimg.com/media/voice_demo.jpg",
        "alt_text": "Muse Voice cooking steps on screen",
    }]}, "quote": {"media": {"photos": [{"url": "https://pbs.twimg.com/media/quote.jpg"}]}}}
    decision = choose_media(tg_news.candidates_from_fx(tweet), story_text=MUSE_TEXT, link=MUSE_URL)
    assert decision["approved"] is True
    assert decision["provenance"] == "verified_demo"
    assert decision["url"].endswith("voice_demo.jpg")
    assert any(row["reason"] == "quoted_post_image" for row in decision["rejected"])


def test_unrelated_alt_and_logo_fail_closed():
    unrelated = choose_media([{
        "url": "https://pbs.twimg.com/media/track.jpg", "kind": "own_photo", "origin": "original",
        "alt": "racetrack paddock and a parked utility vehicle",
    }], story_text=MUSE_TEXT)
    assert unrelated["url"] is None
    assert unrelated["rejected"][0]["reason"] == "unrelated_photo"
    logo = choose_media([{
        "url": "https://pbs.twimg.com/media/logo.jpg", "kind": "own_photo", "origin": "original",
        "alt": "Muse Voice logo",
    }], story_text=MUSE_TEXT, logo_fn=lambda _url: True)
    assert logo["url"] is None
    assert logo["allow_official_capture"] is True
    assert logo["rejected"][0]["reason"] == "logo_or_placeholder"


def test_legacy_photo_without_provenance_is_rejected():
    decision = gate_photo_url("https://pbs.twimg.com/media/real.jpg")
    assert decision["approved"] is False
    assert decision["rejected"][0]["reason"] in {"provenance_missing", "unverified_relevance"}
    kept = gate_photo_url("/data/tweetcap/1.png", provenance="official_capture")
    assert kept["approved"] is True
    assert kept["provenance"] == "official_capture"


def test_basement_joke_is_not_an_openai_update():
    item = {
        "source_text": BASEMENT_TEXT,
        "quoted_text": "It's Friday",
        "author": "thsottiaux",
        "url": BASEMENT_URL,
        "classification": "openai:update/agent/top_tier",
        "followers": 900000,
        "employer": "OpenAI",
        "lane": "breaking",
        "body": "오픈AI가 새 게임을 출시했다\n에이전트가 만든 게임이라 바로 써볼 수 있다",
    }
    verdict = assess_newsworthiness(item)
    assert verdict["allow"] is False
    assert verdict["reason"] == "not_newsworthy_trivial"
    # fame fields are ignored: the same source without them is the same rejection
    slim = dict(item)
    slim.pop("followers")
    slim.pop("employer")
    assert assess_newsworthiness(slim)["reason"] == verdict["reason"]


def test_voice_launch_is_news_but_invented_ai_or_firsthand_is_not():
    base = {"source_text": MUSE_TEXT, "author": "muse", "url": MUSE_URL, "lane": "breaking",
            "body": KO_OK}
    assert assess_newsworthiness(base)["allow"] is True
    invented = dict(base, body="오픈AI가 보이스를 출시했다\n에이전트 기능이라 바로 쓸 수 있다")
    assert assess_newsworthiness(invented)["reason"] == "invented_ai_context"
    tried = dict(base, body="뮤즈 보이스를 직접 써봤다\n요리 순서가 바로 나온다")
    assert assess_newsworthiness(tried)["reason"] == "invented_firsthand"


def test_aggregator_needs_primary_source_and_practical_case_can_pass():
    agg = {"author": "hourly_news", "author_role": "aggregator", "lane": "breaking",
           "source_text": "OpenAI launched a new agent mode today",
           "url": "https://x.com/hourly_news/status/50"}
    assert assess_newsworthiness(agg)["reason"] == "aggregator_unverified"
    grounded = dict(agg, extra_urls=["https://openai.com/index/agent-mode"])
    assert assess_newsworthiness(grounded)["allow"] is True
    practical = {
        "kind": "useful_case",
        "author": "cursor_ai",
        "source_text": "Cursor's agent now applies a multi-file edit from one prompt. You can land a refactor without merging each file by hand.",
        "extra_urls": ["https://cursor.com/docs/agent"],
        "lane": "breaking",
    }
    assert assess_newsworthiness(practical)["allow"] is True
    pr = {"source_text": "We're excited to announce a partnership with Acme.", "author": "openai",
          "url": "https://x.com/openai/status/9", "lane": "breaking"}
    assert assess_newsworthiness(pr)["reason"] == "not_newsworthy_pr"


def test_story_dedup_ignores_account_and_image():
    article = "https://openai.com/index/agent-mode?utm_source=x"
    first = {"url": "https://x.com/openai/status/10", "source_text": "OpenAI launched agent mode " + article}
    second = {"url": "https://x.com/cursor_ai/status/11", "source_text": "same story " + article,
              "image": "https://pbs.twimg.com/media/other.jpg"}
    assert duplicate_reason(story_keys(second), [first])
    keys = story_keys(second)
    assert all("other.jpg" not in key for key in keys)
    assert not duplicate_reason(story_keys({"url": "https://x.com/binance/status/12", "source_text": "Binance will list XYZ at 12:00 UTC"}), [first])


def test_korean_style_rules():
    ok = prepare_korean("<b>🚨 뮤즈 보이스를 열었다</b>\n말로 물어보면 요리 순서까지 이어서 알려준다")
    assert ok["reject"] is False
    assert "emoji" in ok["sanitized"] and "bold" in ok["sanitized"]
    assert "🚨" not in ok["body"] and "<b>" not in ok["body"]
    assert "banned_ending" in prepare_korean("출시함\n바로 쓸 수 있다")["reasons"]
    assert "banned_ending" in prepare_korean("좋네\n바로 쓸 수 있다")["reasons"]
    assert "title_repetition" in prepare_korean("뮤즈 보이스를 열었다\n뮤즈 보이스를 열었다")["reasons"]
    assert "missing_why_it_matters" in prepare_korean("뮤즈 보이스를 열었다")["reasons"]
    assert "mention_list" in prepare_korean("뮤즈 보이스를 열었다\n@sama @thsottiaux 가 같이 썼다")["reasons"]


def test_privileged_claim_without_source_fails_closed():
    verdict = assess_newsworthiness({
        "classification": "openai:update/agent/top_tier",
        "author": "thsottiaux",
        "body": "오픈AI 업데이트를 정리했다\n이용자가 바로 쓸 수 있다",
        "lane": "hot",
    })
    assert verdict["allow"] is False
    assert verdict["evidence"] == "missing"
