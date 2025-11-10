# Yapping Bot v1 Blueprint / 야핑 봇 v1 청사진

## 1. Objectives / 목표
- **Primary**: Turn incoming market signals into short Korean/English copy, post to X (Twitter) and Telegram, and store a log.  
  **주요 목표**: 수집한 시장 신호를 한·영 짧은 카피로 변환하고 X(트위터)와 텔레그램에 게시하며 로그를 저장한다.
- **Secondary (stretch)**: Queueing, richer media handling, additional channels, content quality filters, and A/B experimentation.  
  **확장 목표**: 큐 관리, 미디어 처리 고도화, 추가 채널 연동, 콘텐츠 품질 필터링, A/B 카피 실험 등을 순차적으로 도입한다.

## 2. High-Level Flow / 상위 흐름
1. **Ingest** events from webhooks, RSS feeds, or manual drop zones (Google Sheets, Notion, Telegram).  
   웹훅, RSS, 수동 입력(Google Sheets, Notion, Telegram)에서 이벤트를 수집한다.
2. **Normalize** payloads into a consistent structure and deduplicate by URL hash.  
   페이로드를 표준 구조로 정규화하고 URL 해시로 중복을 제거한다.
3. **Filter** with whitelist/blacklist rules and optional regex-based compliance checks.  
   화이트/블랙리스트와 정규식 기반 규칙으로 필터링한다.
4. **Enrich** with OpenAI: generate KO/EN copy + hashtags and collect metadata (token usage, language detection).  
   OpenAI로 한·영 카피와 해시태그를 생성하고 토큰 사용량 등 메타데이터를 수집한다.
5. **Schedule** via direct post (immediate) or queue (delayed + random sleep).  
   즉시 게시 또는 큐잉(지연 + 랜덤 슬립) 방식으로 스케줄링한다.
6. **Distribute** to X API (or HTTP call), Telegram Bot, and optional future channels.  
   X API(또는 HTTP), 텔레그램 봇, 향후 확장 채널로 배포한다.
7. **Persist & Observe** by logging to Google Sheets/Data Store and notifying owner via Telegram DM.  
   Google Sheets/데이터 스토어에 기록하고 텔레그램 DM으로 알림을 보낸다.
8. **Engage** by discovering external tweets, scoring them, and queueing like/reply actions within configured limits.  
   외부 트윗을 탐색·점수화해 설정된 한도 내에서 좋아요/댓글 작업을 큐잉한다.

## 3. Component Breakdown / 구성 요소 세부
| Layer / 레이어 | Responsibilities / 역할 | First Implementation Choice / 초기 구현 선택 | Notes / 비고 |
| --- | --- | --- | --- |
| Ingestion | Webhook endpoint, RSS poller, manual drop connectors | Make Scenario A (webhook) + Scenario B (RSS) | For local dev, mirror Make payloads with curl/postman. / 로컬 테스트 시 curl·Postman으로 페이로드 미러링 |
| Persistence | Deduplication store & history log | Make Data Store + Google Sheets | Use URL MD5 as key; keep status + timestamps. / URL MD5를 키로 사용하고 상태·타임스탬프 저장 |
| Filtering | Keyword routing, blacklist, compliance regex | Make Router modules | Parameterize patterns via shared table for easy tuning. / 공유 테이블로 패턴 파라미터화 |
| AI Copy | Generate KO/EN copy, hashtags | OpenAI Chat completions | Temperature ~0.7, max tokens 300, keep prompt versioned. / 온도 0.7, 최대 토큰 300, 프롬프트 버전화 |
| Rate Management | Random delays, retry, queue draining | Make Sleep module + Scheduler | Separate fast path (immediate) vs queue path. / 즉시 경로와 큐 경로 분리 |
| Distribution | Post to X, Telegram, (later) Discord/Bluesky | Make native modules or HTTP request | Prefer Telegram first if X API access not ready. / X API 없으면 텔레그램 우선 |
| Observability | Success/fail logs, alerting | Google Sheets + Telegram DM | Include error payloads for manual debugging. / 에러 페이로드 포함해 디버깅 지원 |
| Engagement | Tweet discovery, scoring, like/reply execution | FastAPI service + Twitter API | Daily limits, blacklist, admin controls. /

## 4. Scenario Designs / 시나리오 설계
### Scenario A – Webhook → Immediate Yapping / 시나리오 A – 웹훅 → 즉시 야핑
- **Trigger**: Custom webhook `/yap/push`.  
  **트리거**: 커스텀 웹훅 `/yap/push`
- **Steps / 단계**:
  1. Text aggregator composes single prompt string from title/url/source/tags.  
     텍스트 집계기가 제목·URL·소스·태그를 하나의 프롬프트 문자열로 합친다.
  2. Data Store lookup by `hash(url)`; exit if found.  
     데이터 스토어에서 `hash(url)`로 조회하고 존재 시 종료한다.
  3. Router checks whitelist/blacklist patterns (white first, then black).  
     라우터가 화이트리스트→블랙리스트 순으로 패턴을 검사한다.
  4. OpenAI call using prompt template `PROMPT_KO_EN_V1`.  
     `PROMPT_KO_EN_V1` 템플릿으로 OpenAI를 호출한다.
  5. Sleep for `random(15;120)` seconds.  
     `random(15;120)` 초 동안 대기한다.
  6. Post to X via API (if credentials available) or branch to Telegram-only.  
     X API 자격 증명이 있으면 게시하고 없으면 텔레그램 전용 경로로 분기한다.
  7. Send Telegram channel message (toggle language variants).  
     텔레그램 채널에 메시지를 전송하며 언어 버전을 선택한다.
  8. Write consolidated record to Data Store and append row in Google Sheets.  
     통합 기록을 데이터 스토어에 저장하고 Google Sheets에 행을 추가한다.
  9. Optional Telegram DM to owner summarizing result.  
     선택적으로 텔레그램 DM으로 결과 요약을 발송한다.

### Scenario B – RSS → Queue / 시나리오 B – RSS → 큐
- **Trigger**: Scheduler every 10 minutes, 09:00–23:00 KST.  
  **트리거**: 매 10분, KST 09:00–23:00 스케줄러 실행
- **Steps / 단계**:
  1. Watch multiple RSS feeds; aggregate new items.  
     여러 RSS 피드를 감시하고 신규 항목을 수집한다.
  2. Run same dedupe/filter/copy modules as Scenario A.  
     시나리오 A와 동일한 중복 제거·필터·카피 모듈을 실행한다.
  3. Instead of direct posting, insert rows into Google Sheets `Queue` tab with `status=pending`.  
     즉시 게시 대신 Google Sheets `Queue` 탭에 `status=pending` 행을 적재한다.
- **Queue Drainer** (separate scenario) / **Queue Drainer**(별도 시나리오):
  1. Scheduler every 5 minutes.  
     5분마다 스케줄러 실행.
  2. Fetch top N `pending` rows that match current slot capacity.  
     현재 슬롯과 맞는 상위 N개의 `pending` 행을 가져온다.
  3. Random sleep per post; publish to X/Telegram.  
     게시물마다 랜덤 지연 후 X/텔레그램에 게시한다.
  4. Update row to `status=posted` and log timestamp.  
     행을 `status=posted`로 갱신하고 타임스탬프를 기록한다.
  5. On error, set `status=error` + `error_msg`, notify owner DM.  
     오류 시 `status=error`, `error_msg`를 기록하고 소유자에게 DM을 보낸다.

### Scenario C – Tweet Discovery & Engagement / 시나리오 C – 트윗 탐색·상호작용
- **Trigger**: Background worker every `ENGAGEMENT_POLL_INTERVAL_SECONDS`.  
  **트리거**: `ENGAGEMENT_POLL_INTERVAL_SECONDS` 간격으로 상호작용 워커 실행
- **Steps / 단계**:
  1. For each configured search query, call X recent search API.  
     설정된 검색어마다 X 최근 검색 API를 호출한다.
  2. Filter out blacklist keywords and already-seen tweet IDs.  
     블랙리스트 키워드 및 기존 처리 트윗을 제외한다.
  3. Score tweets using public metrics (likes/retweets/replies) with configurable weights; drop below-threshold results.  
     공개 지표(좋아요·리트윗·댓글)에 가중치를 적용해 점수를 계산하고 임계치 미만 항목을 제거한다.
  4. Create engagement tasks with recommended action (like vs reply) and reason metadata.  
     추천 작업(좋아요/댓글)과 사유 메타데이터를 포함한 상호작용 태스크를 생성한다.
  5. Worker enforces daily like/reply limits, performs API calls, and records outcomes + logs.  
     워커는 일일 좋아요/댓글 한도를 지키며 API 호출을 수행하고 결과를 로그로 남긴다.

## 5. Data Contracts / 데이터 계약
### Base Payload (shared across scenarios) / 기본 페이로드
```json
{
  "title": "SOL funding spike",
  "url": "https://...",
  "source": "hyperliquid",
  "lang": "en",
  "tags": ["SOL", "funding"]
}
```
위 구조는 모든 시나리오가 공유하는 최소 입력이며, 필요 시 필드를 확장한다.  
The above schema is the minimal shared input; extend fields as needed.

### Data Store / Sheet Fields / 데이터 스토어·시트 필드
| Field / 필드 | Type / 타입 | Description / 설명 |
| --- | --- | --- |
| id | auto | Primary key (Data Store) / 데이터 스토어 기본 키 |
| hash | string | `md5(url)` |
| title | string | Original headline / 원문 제목 |
| url | string | Canonical link / 표준 링크 |
| source | string | Origin system / 출처 시스템 |
| lang | string | Incoming language / 입력 언어 |
| tags | string | CSV tags / CSV 태그 |
| text_ko | string | Generated Korean copy / 생성된 한국어 카피 |
| text_en | string | Generated English copy / 생성된 영어 카피 |
| hashtags | string | `#tag1 #tag2 #tag3` |
| channels | string | `x,tg` |
| status | string | `pending|posted|skipped|error` |
| posted_at | datetime | ISO timestamp / ISO 타임스탬프 |
| error_msg | string | Error snippet / 오류 메시지 |
| token_usage | int | (Optional) total tokens / (옵션) 토큰 사용량 |

## 6. Prompt Template v1 / 프롬프트 템플릿 v1
```
Role: Crypto "yapping" copywriter.
Requirements:
- Output three blocks: 1) Korean sentence (150-200 chars), 2) English sentence (120-180 chars), 3) Three hashtags (#tag format).
- Ban exaggerated profit language, FUD, or defamatory phrasing. Keep numbers conservative.
- Do not embed URLs inside sentences.
Input: {{title}} | source={{source}} | tags={{tags}}
Output:
[KO] ...
[EN] ...
[TAG] #...
```
```
역할: 크립토 ‘야핑’ 카피라이터.
조건:
- 세 블록을 출력한다: 1) 한국어 문장 1개(150~200자), 2) 영어 문장 1개(120~180자), 3) 해시태그 3개(#tag 형식).
- 과장된 수익 표현, FUD, 비방성 문구를 금지하고 숫자는 보수적으로 쓴다.
- 문장 내부에 URL을 삽입하지 않는다.
입력: {{title}} | source={{source}} | tags={{tags}}
출력:
[KO] ...
[EN] ...
[TAG] #...
```

Maintain prompt versions (e.g., `PROMPT_KO_EN_V1`) and log them with each record for traceability.  
프롬프트 버전을 관리(예: `PROMPT_KO_EN_V1`)하고 각 기록에 함께 저장해 추적성을 확보한다.

## 7. Safety & Rate Controls / 안전장치 및 속도 제어
- **Deduplication**: Enforce via hash before AI call to save tokens.  
  **중복 제거**: OpenAI 호출 전 해시로 차단해 토큰 낭비를 줄인다.
- **Rate limiting**: Sleep module for random delays; queue to throttle further.  
  **레이트 리밋**: 슬립 모듈로 랜덤 지연을 주고 큐로 추가 제어한다.
- **Retry policy**: On 429/5xx from X/Telegram, apply Make error handler → Sleep (60s) → Retry up to 3 times.  
  **재시도 정책**: X/텔레그램에서 429/5xx 발생 시 에러 핸들러 → 60초 슬립 → 최대 3회 재시도.
- **Compliance**: Maintain blacklist regex for financial guarantees and NSFW; add manual review switch by skipping auto post if flagged.  
  **컴플라이언스**: 금융 보장·NSFW 등 블랙리스트 정규식을 유지하고 감지 시 자동 게시를 중단해 수동 검수를 거친다.
- **Manual override**: Provide `Manual trigger` scenario to approve queued items when needed.  
  **수동 개입**: 필요 시 `Manual trigger` 시나리오로 큐 항목을 승인·게시한다.

## 8. Implementation Roadmap / 구현 로드맵
1. **Infrastructure Setup / 인프라 준비**
   - Create Make project, custom webhook, and base Data Store tables.  
     Make 프로젝트와 커스텀 웹훅, 기본 데이터 스토어 테이블을 생성한다.
   - Prepare Google Sheets workbook with `Log` and `Queue` tabs.  
     `Log`, `Queue` 탭을 포함한 Google Sheets를 준비한다.
   - Secure API credentials: OpenAI, Telegram Bot; confirm X access.  
     OpenAI, 텔레그램 봇 토큰을 확보하고 X 접근 권한을 확인한다.
2. **Scenario A Delivery / 시나리오 A 구축**
   - Build sequential modules as described.  
     설계된 순서대로 모듈을 구성한다.
   - Validate with sample webhook payloads; confirm dedupe and posting.  
     샘플 웹훅 페이로드로 테스트하고 중복 제거·게시를 확인한다.
   - Instrument logging + owner DM notifications.  
     로깅과 관리자 DM 알림을 설정한다.
3. **Scenario B & Queue / 시나리오 B 및 큐**
   - Configure RSS ingestion and queue writer.  
     RSS 수집 및 큐 기록 모듈을 설정한다.
   - Implement Queue Drainer with posting capacity controls.  
     게시 용량 제어가 포함된 Queue Drainer를 구현한다.
4. **Hardening / 안정화**
   - Add regex filters, manual approval toggle, and retry branches.  
     정규식 필터, 수동 승인 스위치, 재시도 분기를 추가한다.
   - Track token usage and error metrics in Sheets.  
     시트에서 토큰 사용량과 오류 지표를 추적한다.
5. **v2 Candidates / v2 후보**
   - Media fetching + upload.  
     미디어 수집 및 업로드.
   - Hashtag presets per topic (sheet-driven).  
     시트 기반 토픽별 해시태그 프리셋.
   - Multi-account routing, A/B testing, moderation scoring.  
     다계정 라우팅, A/B 테스트, 모더레이션 스코어링.

## 9. Admin Dashboard / 어드민 대시보드
- FastAPI server-rendered UI (`/admin`) using HTMX for live queue refresh, manual approve/skip buttons, and bilingual labels.  
  HTMX를 활용한 FastAPI 서버 렌더링 UI(`/admin`)에서 실시간 큐 갱신과 승인/스킵 버튼을 제공하며, 한·영 병기 라벨을 유지한다.
- Queue table pulls directly from the same repository layer, ensuring consistent dedupe/filter state.  
  큐 테이블은 동일한 레포지토리 계층을 사용하므로 중복 제거·필터 상태를 일관되게 반영한다.
- Log panel surfaces `post_logs` events for rapid troubleshooting; optional Google Sheets logging remains in sync.  
  로그 패널이 `post_logs` 이벤트를 노출해 빠른 트러블슈팅을 지원하며 Google Sheets 로깅과 동기화된다.
- HTMX actions hit `/admin/queue/*` endpoints which reuse core pipeline/approval logic, avoiding duplicate pathways.  
  HTMX 액션은 핵심 파이프라인/승인 로직을 재사용하는 `/admin/queue/*` 엔드포인트를 호출해 중복 구현을 방지한다.
- Engagement widgets show discovery queue, action logs, and metrics with HTMX refresh for monitoring like/reply automation.  
  상호작용 위젯이 트윗 큐·작업 로그·지표를 표시하며 HTMX 새로고침으로 좋아요/댓글 자동화를 관제할 수 있다.

## 10. Local Companion Service (Optional v1.5) / 로컬 컴패니언 서비스(선택 v1.5)
If more control is needed than Make provides, scaffold a lightweight Node.js (NestJS) or Python (FastAPI) service that:  
Make로는 부족한 제어가 필요할 경우, 다음 역할을 수행하는 경량 Node.js(NestJS) 또는 Python(FastAPI) 서비스를 고려한다.
- Receives webhook posts, handles dedupe/filter, and calls OpenAI.  
  웹훅을 받아 중복 제거·필터링을 수행하고 OpenAI를 호출한다.
- Persists state in Supabase/Firestore or local PostgreSQL.  
  Supabase/Firestore 또는 로컬 PostgreSQL에 상태를 저장한다.
- Publishes to Make via webhook for queueing or directly to X/Telegram using official APIs.  
  큐잉을 위해 Make로 웹훅 전송하거나 공식 API로 X/텔레그램에 직접 게시한다.

### Mindshare Sync Workflow / 마인드셰어 동기화 워크플로우
- `scripts/mindshare_sync.py` calls the public Gomtu REST API plus Kaito’s Supabase + `target-user.json` feeds to assemble leaderboard snapshots.  
  `scripts/mindshare_sync.py`는 공개 Gomtu REST API와 Kaito의 Supabase·`target-user.json` 피드를 호출해 리더보드 스냅샷을 수집한다.
- Each run refreshes `mindshare_targets` with per-source entries (Gomtu stats, Supabase leaderboard, Kaito target list) and marks Inner Circle handles.  
  실행 시마다 `mindshare_targets` 테이블을 소스별 데이터(고무투 통계, Supabase 리더보드, Kaito 타깃 리스트)로 갱신하고 이너써클 계정을 표시한다.
- Schedule the script (e.g., cron) every few hours to keep `/admin` dashboards and engagement heuristics current.  
  몇 시간 간격으로 스케줄링하면 `/admin` 대시보드와 인게이지먼트 휴리스틱을 최신 상태로 유지할 수 있다.

### Priority Targeting Layer / 우선 타기팅 레이어
- `data/project_targets.yaml` defines weights, keywords, and handle groups (official vs community) for projects we want to pre-emptively cover.  
  `data/project_targets.yaml`에서 선점하고 싶은 프로젝트의 가중치, 키워드, 공식/커뮤니티 핸들을 정의한다.
- `ProjectPriorityLoader` hydrates those rules, merges Gomtu Supabase mindshare data to avoid saturated topics, and surfaces upcoming campaigns via the timeline cache.  
  `ProjectPriorityLoader`가 규칙을 불러와 Supabase 마인드셰어 데이터를 결합하고, 타임라인 캐시로 곧 시작될 캠페인을 확인해 포화된 주제를 피한다.
- Run `scripts/project_priority_sync.py` (Playwright) to refresh Gomtu timeline events; results feed the `/admin` priority board and engagement scoring bonuses.  
  `scripts/project_priority_sync.py`(Playwright 필요)를 실행하면 Gomtu 타임라인 이벤트를 갱신하고, 이는 `/admin` 우선 보드와 상호작용 점수 보너스로 반영된다.

Keep this optional until Make capabilities are exhausted; the current design favors no-code velocity.  
Make 기능이 충분할 때까지는 선택 사항으로 두되, 현재 설계는 노코드 속도에 초점을 둔다.
