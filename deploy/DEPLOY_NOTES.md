# Snapshot-relative deployment notes

Do not deploy this branch's `app/main.py`. Do not restart, post, or change credentials.
Take a production backup before any later approved apply. This patch was not applied
to `/home/max/yapping-bot`.

## What is wrong with "just use origin/main"

`origin` only has `main` (`2e84ab90233e5c378b474f85b0f2d05221752aa1`, pushed 2026-03-26).
There is no remote `v2` and no commit `7582ecfda4ca56a86641df654eb6f2d416c24cfa`.
The default-branch app is a different FastAPI service. It does not contain
`tg_post.py`, `tg_route.py`, `tg_storyline.py`, or `tg_timing.py`.

Production `/home/max/yapping-bot` was reported dirty on local `v2` at that missing
commit. The four service files were untracked. Their SHA256 values match
`deploy/BASELINE.txt` and `deploy/snapshots/`.

## Apply, later, only after backup and a hash check

From a copy of the production tree, confirm the four files still match the baseline
hashes. If any hash differs, stop: the live file moved and this patch is stale.

```bash
sha256sum app/services/tg_post.py app/services/tg_route.py \
  app/services/tg_storyline.py app/services/tg_timing.py
patch -p1 --dry-run < deploy/snapshot-relative.patch
patch -p1 < deploy/snapshot-relative.patch
```

The patch updates those four files and adds:

- `app/quality/tg_news.py`
- `app/quality/x_own.py`
- `app/quality/msg_status.py`
- `app/quality/__init__.py`
- `app/routers/msg_status.py`

It does not modify `app/main.py`, `targets.yaml`, or secrets.

`app/routers/msg_status.py` is not mounted until the live router file is changed.
Snapshot that file first (see below). Then call `build_hub_summary` / `build_tg_status`
from the existing handlers, or include the router if the live app does not already
own those paths. Do not replace a live handler with the default-branch `app/main.py`.

## Extra exact-file snapshots still required

These are imported or implied by the live path and were not in the four files.
Do not treat origin/main copies as substitutes.

| Need | Why |
| --- | --- |
| Live `app/main.py` or the module that serves `GET /api/tg_status` and `GET /api/hub_summary` | Mount the status builders without clobbering production. `binance_ann` is filled by that live source; this patch leaves it null when the source is absent. |
| `app/services/ai_news.py` | `post_tg`, `_bot_token`, `_strip_company`, and any media pick that does not call `tg_post.decide_media` / `tg_post.send`. |
| The module that assigns `openai:update` / `top_tier` | That label is not produced in the four snapshots. `thsottiaux` is only an agent-handle weight inside `tg_route.py`. Pass `source_text` and `classification` into `deliver`. |
| `app/services/sourcing.py` | Official-handle and relay/aggregator checks. The gate uses `author_role` / `aggregator` until this file is present. |
| `app/services/breaking.py` | `KEY_PEOPLE` for storyline. |
| `app/services/quality.py` | Existing text validator and fxtwitter GET helper. |
| `app/services/llm.py`, `data_reports.py`, `tg_curate.py`, `tg_forward.py`, `tg_card.py` | Called by the four services. `tg_card.card` is no longer used as a placeholder image. `author_info` is unchanged. |
| `app/accounts.py`, `app/config.py`, `app/db.py`, `targets.yaml` | Package imports and caps. Not the same as this repo's `app/config.py`. |
| Chrome-extension executor for `@cblabx` | Own-content drafts stop here. X API stays off. This module does not launch a browser or read cookies. The default-branch `app/services/posters/twitter.py` is not that executor. |
| `tools/anncap/tweetcap.js` | Only for an official logo/brand card capture. A rejected video frame is not screenshotted. |

Also snapshot any other untracked file in the dirty production tree before an apply.
This patch cannot see them.

## Runtime status

`policy_enforcement_status` stays `not_applied` until the running sender calls
`set_enforcement_attestation(status, observed=True)`. Preparation of this patch
is not a claim that production is live. Known posts 795 and 796 are reported as
`fix_status: prepared` until that attestation is `testing` or `live`.

Missing candidate, rejection, queue, cap, and own-content history is null, with
a reason. Partial rejection rows are under `partial_rejections` and are not a pass rate.
