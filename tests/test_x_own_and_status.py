"""English own-content drafts never publish. Status metrics stay null when unknown."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.quality.msg_status import build_hub_summary, build_tg_status, response_leaks_private
from app.quality.tg_news import set_enforcement_attestation
from app.quality.x_own import OwnContentPublishBlocked, X_API_ENABLED, draft_own_post, operational_status, publish

def _router_module():
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "msg_status.py"
    spec = importlib.util.spec_from_file_location("yb_msg_status_router", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

SOURCE = (
    "Cursor's agent now applies a multi-file edit from one prompt. "
    "You can land a refactor without merging each file by hand. "
    "https://cursor.com/docs/agent"
)
CHANGE = "Cursor's agent now applies a multi-file edit from one prompt."
WHY = "That matters because you can land a refactor without merging each file by hand."


def _item(**extra):
    base = {
        "source_text": SOURCE,
        "what_changed": CHANGE,
        "why_it_matters": WHY,
        "primary_urls": ["https://cursor.com/docs/agent"],
        "author": "cursor_ai",
        "official": True,
        "news_kind": "useful_case",
    }
    base.update(extra)
    return base


def test_grounded_english_draft_is_not_a_quote_or_translation():
    drafted = draft_own_post(_item(), config={"enabled": True, "daily_cap": 2}, posted_today=0)
    assert drafted["ok"] is True
    assert drafted["ready_for_executor"] is True
    assert drafted["x_api"] == "off"
    assert drafted["handoff"]["invoked"] is False
    assert drafted["handoff"]["cookies"] is False
    assert drafted["handoff"]["browser"] is False
    assert drafted["handoff"]["executor"] == "chrome_extension"
    assert "QT" not in drafted["text"]
    assert CHANGE.split()[0] in drafted["text"]
    assert X_API_ENABLED is False


def test_publish_is_blocked_and_does_not_return_an_id():
    with pytest.raises(OwnContentPublishBlocked):
        publish({"text": "hello"})


def test_rejects_qt_opinion_ungrounded_and_duplicate():
    qt = draft_own_post(_item(what_changed="QT: " + CHANGE, mode="qt"))
    assert "not_original" in qt["reasons"]
    opinion = draft_own_post(_item(why_it_matters="I tested it and it is better than everything else."))
    assert "invented_opinion_or_personal_testing" in opinion["reasons"]
    invented = draft_own_post(_item(what_changed="Cursor shipped a secret model called Orion-9."))
    assert "change_not_in_source" in invented["reasons"]
    seen = [{"url": "https://cursor.com/docs/agent", "ok": True}]
    dup = draft_own_post(_item(), seen=seen, config={"enabled": True, "daily_cap": 2}, posted_today=0)
    assert dup["ok"] is False
    assert any(r.startswith("duplicate story") for r in dup["reasons"])


def test_missing_cap_history_is_not_zero():
    drafted = draft_own_post(_item(), config={"enabled": True, "daily_cap": 2})
    assert drafted["ok"] is True
    assert drafted["ready_for_executor"] is False
    assert drafted["cap"]["posted_today"] is None
    assert drafted["cap"]["allowed"] is None
    ramp = draft_own_post(_item(), config={"enabled": True, "ramp": {"steps": [1, 2, 4]}})
    assert ramp["cap"]["allowed"] is None
    assert "day 0" in ramp["cap"]["reason"]
    status = operational_status(None, None)
    assert status["posted_today"] is None
    assert status["metrics"] is None
    assert status["x_api"] == "off"
    assert status["available"] is False


def test_hub_summary_nulls_partial_rejections_and_redaction():
    set_enforcement_attestation(None)
    try:
        summary = build_hub_summary({})
        tg = summary["telegram"]
        assert tg["policy_enforcement_status"] == "not_applied"
        assert tg["sourcing"]["candidates"] is None
        assert tg["sourcing"]["pass_rate"] is None
        assert tg["sourcing"]["unavailable_reason"]
        assert tg["observed_rejections"] is None
        assert tg["pending"]["count"] is None
        assert tg["today"]["by_channel_lane"] is None
        assert summary["own_content"]["posted_today"] is None
        assert summary["own_content"]["metrics"] is None
        issues = {row["post_url"]: row for row in tg["quality_issues"]}
        assert issues["https://t.me/cblaboratory/795"]["certainty"] == "confirmed"
        assert issues["https://t.me/cblaboratory/795"]["fix_status"] == "prepared"
        assert issues["https://t.me/cblaboratory/796"]["fix_status"] == "prepared"

        partial = build_hub_summary({"telegram": {"partial_rejections": [{"reason": "not_newsworthy_trivial", "count": 2}]}})
        assert partial["telegram"]["observed_rejections"] is None
        assert partial["telegram"]["sourcing"]["pass_rate"] is None
        assert partial["telegram"]["partial_rejections"][0]["coverage"] == "partial"

        private = "https://t.me/c/123456789/42"
        leaked = build_tg_status({
            "enabled": True,
            "sent_events": [{
                "at": "2026-11-07T12:00:00+09:00",
                "channel": "-100123456789",
                "lane": "breaking",
                "ok": True,
                "telegram_url": private,
                "url": "https://user:pass@x.com/openai/status/10?token=sekret&utm_source=x",
                "headline": "visible headline",
            }, {
                "at": "2026-11-07T12:05:00+09:00",
                "channel": "@cblaboratory",
                "lane": "hot",
                "ok": True,
                "telegram_url": "https://t.me/cblaboratory/795",
                "url": "https://x.com/Muse/status/2118411960337289583",
            }],
        })
        assert leaked["today_by_channel"]["private group"] == ["breaking"]
        assert isinstance(leaked["today_by_channel"]["@cblaboratory"], list)
        assert all(isinstance(v, list) for v in leaked["today_by_channel"].values())
        assert leaked["today_by_lane"]["hot"] == ["@cblaboratory"]
        blob = json.dumps(leaked)
        assert "123456789" not in blob
        assert "sekret" not in blob
        assert "user:pass" not in blob
        assert "https://t.me/cblaboratory/795" in blob
        assert "https://x.com/openai/status/10" in blob or "https://x.com/Muse/status/2118411960337289583" in blob
        assert response_leaks_private(leaked) == []
        assert leaked["binance_ann"] is None
        assert leaked["enabled"] is True

        unknown = build_tg_status({})
        assert unknown["enabled"] is None
        assert unknown["today_by_channel"] is None
        assert unknown["binance_ann"] is None

        set_enforcement_attestation("live", observed=True)
        live = build_hub_summary({})
        assert live["telegram"]["policy_enforcement_status"] == "live"
        assert live["telegram"]["quality_issues"][0]["fix_status"] == "live"
        set_enforcement_attestation("live", observed=False)
        assert build_hub_summary({})["telegram"]["policy_enforcement_status"] == "not_applied"
    finally:
        set_enforcement_attestation(None)


def test_status_routes_are_read_only():
    routes = _router_module()
    app = FastAPI()
    app.include_router(routes.router)
    routes.set_snapshot_provider(lambda: {})
    client = TestClient(app)
    hub = client.get("/api/hub_summary")
    status = client.get("/api/tg_status")
    assert hub.status_code == 200
    assert status.status_code == 200
    body = hub.json()
    assert "telegram" in body and "own_content" in body
    assert body["telegram"]["sourcing"]["pass_rate"] is None
    assert status.json()["today_by_channel"] is None
    routes.set_snapshot_provider(None)
