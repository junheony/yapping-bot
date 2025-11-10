# Yapping Bot Service / 야핑 봇 서비스

## Overview / 개요
FastAPI-based automation that ingests crypto signals (webhook + RSS), applies filters, generates KO/EN copy via OpenAI, queues items with randomised posting, and publishes to Telegram and X with Google Sheets logging.  
웹훅·RSS로 유입된 크립토 신호를 필터링한 뒤 OpenAI로 한·영 카피를 생성하고, 랜덤 딜레이 큐를 거쳐 텔레그램과 X에 게시하며 Google Sheets에 기록하는 FastAPI 기반 자동화 서비스입니다.

### Features / 주요 기능
- Webhook ingestion (`POST /yap/push`) with duplicate detection, whitelist/blacklist, and manual review routing.  
  웹훅 입력 시 중복 검사, 화이트·블랙리스트 필터, 수동 검수 분기 지원.
- Background RSS poller with active-hours window, auto-queueing, and manual-review triggers.  
  활성 시간대 기반 RSS 폴러가 자동 큐잉을 수행하고 필요 시 수동 검수 요청.
- Async queue worker that drains pending posts with random delays and retries.  
  비동기 큐 워커가 랜덤 지연과 재시도 전략으로 대기 항목을 처리.
- Telegram + X posting (OAuth1) with bilingual formatting and owner DM notifications.  
  텔레그램·X(OAuth1) 동시 게시, 한·영 포맷 유지, 관리자 DM 알림.
- Google Sheets logging for audit trail plus optional queue sheet appends.  
  Google Sheets에 이력·큐 기록을 남겨 관제 용이.
- Priority project targeting with configurable weights, keywords, and Kaito onboarding calendar awareness.  
  가중치·키워드·온보딩 캘린더를 설정할 수 있는 우선 공략 프로젝트 타기팅 기능.

## Prerequisites / 사전 준비
- Python 3.9+
- OpenAI API key
- Telegram Bot token & target channel
- (Optional) X API credentials with write access (OAuth1 user context)
- (Optional) X API bearer token + user ID for tweet discovery/likes/replies
- (Optional) Google Sheets service account JSON + Sheet IDs

## Setup / 설정
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp .env.example .env  # Fill in secrets
```

## Run / 실행
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```
- Webhook: `POST /yap/push`
- Queue list: `GET /yap/queue?manual_only=`true|false
- Manual approve: `POST /yap/queue/{hash}/approve?process_immediately=true|false`
- Manual skip: `POST /yap/queue/{hash}/skip?reason=...`
- Health check: `GET /health`
- Root info: `GET /`
- Admin GUI: `GET /admin`

## Notes / 참고
- Default random delay before posting is 15–120s (`POST_RANDOM_DELAY_*`); set both to `0` for local tests.  
  게시 전 랜덤 지연 기본값은 15~120초이며 테스트 시 두 값을 `0`으로 설정하면 비활성화됩니다.
- Queue worker interval + batch size are configurable via `QUEUE_POLL_INTERVAL_SECONDS`, `QUEUE_BATCH_SIZE`.  
  큐 워커 간격과 배치 크기는 `QUEUE_POLL_INTERVAL_SECONDS`, `QUEUE_BATCH_SIZE`로 조절할 수 있습니다.
- RSS poller activates only during the hours defined in `RSS_ACTIVE_HOURS_LOCAL`.  
  RSS 폴러는 `RSS_ACTIVE_HOURS_LOCAL`에 지정된 시간대에만 작동합니다.
- Google Sheets logging is optional; leave related env values empty to disable.  
  Google Sheets 로깅은 옵션이므로 환경 변수 미기입 시 자동으로 비활성화됩니다.
- X API write access requires a paid Basic/Elevated plan; keep `TWITTER_ENABLED=false` until credentials are available.  
  X API 글쓰기 권한은 유료 Basic/Elevated 플랜이 필요하므로 자격 확보 전까지 `TWITTER_ENABLED=false` 상태를 유지하세요.
- Admin dashboard metrics (`/admin`) summarize total processed, posted, pending, skipped, and error counts, along with channel-specific postings.  
  `/admin` 대시보드의 지표 카드에서 총 처리량/게시/대기/건너뜀/오류 개수와 채널별 게시 횟수를 확인할 수 있습니다.
- Engagement metrics keep track of discovered tweets and completed likes/replies while showing remaining daily capacity.  
  상호작용 지표는 탐색된 트윗과 완료된 좋아요·댓글 수, 남은 일일 한도를 확인할 수 있게 합니다.

## Next Steps / 다음 단계
1. Fill `.env` with OpenAI, Telegram, X (옵션), Google Sheets (옵션) credentials; run `uvicorn` and test `POST /yap/push`.  
   `.env`에 OpenAI·텔레그램·X(선택)·Google Sheets(선택) 자격을 입력하고 `uvicorn` 실행 후 `POST /yap/push`를 시험하세요.
2. Connect RSS feeds in `RSS_FEEDS` and confirm queue worker drains new items automatically.  
   `RSS_FEEDS`에 RSS를 등록하고 큐 워커가 자동으로 처리하는지 확인하세요.
3. Provide X API credentials (`TWITTER_*` keys plus `TWITTER_BEARER_TOKEN`, `TWITTER_USER_ID`) and set `TWITTER_ENABLED=true` once the paid plan is active; the engagement worker will begin scheduling likes/replies within daily limits.  
   유료 플랜을 확보하면 `TWITTER_*` 키와 `TWITTER_BEARER_TOKEN`, `TWITTER_USER_ID`를 설정하고 `TWITTER_ENABLED=true`로 변경해 상호작용 워커가 일일 한도 내에서 좋아요/댓글을 수행하도록 하세요.
4. Use `/yap/queue` endpoints or the admin GUI for manual approvals/skips, and monitor Sheets logs for auditing if enabled.  
   수동 승인/스킵은 `/yap/queue` API나 어드민 GUI에서 처리하고 Sheets 로그를 병행 확인하세요.
5. Review engagement tables/logs on the admin dashboard to track which tweets were discovered, actioned, or skipped; refresh buttons update the view instantly via HTMX.  
   어드민 대시보드의 상호작용 테이블/로그를 통해 탐색·처리·건너뛴 트윗 현황을 파악하고, HTMX 새로고침 버튼으로 즉시 갱신하세요.
6. Refresh priority watchlists & campaign timeline (optional but recommended): `python scripts/project_priority_sync.py` (requires Playwright). Edit `data/project_targets.yaml` to adjust weights, handles, or keywords.  
   우선 공략 프로젝트·캠페인 타임라인을 갱신하려면 `python scripts/project_priority_sync.py`를 실행하세요(Playwright 필요). `data/project_targets.yaml`에서 가중치·핸들·키워드를 조정할 수 있습니다.
7. Sync Mindshare targets periodically: `python scripts/mindshare_sync.py`. The admin “Mindshare Targets” board updates automatically afterwards.  
   Mindshare 타깃을 주기적으로 동기화하려면 `python scripts/mindshare_sync.py`를 실행하세요. 실행 후 어드민 보드가 자동으로 갱신됩니다.
8. Collect research tweets for qualitative analysis with `python scripts/collect_posts.py`; captured posts are stored in `research_posts` for further study.  
   정성 분석용으로 `python scripts/collect_posts.py`를 실행하면 최신 트윗이 `research_posts` 테이블에 저장됩니다.
