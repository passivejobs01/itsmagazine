# Content Tools API 문서

> FastAPI 서버 (포트 8003). 공개 주소는 환경에 맞게 설정(FASTAPI_PUBLIC_URL).

---

## 시스템 구성

```
[자동화 서버(n8n 등)]      [FastAPI 콘텐츠 서버]         [로컬 AI]
  스케줄 트리거          →   FASTAPI_PUBLIC_URL:8003   →   Ollama (OLLAMA_HOST)
                              faster-whisper STT 내장        모델: SUMMARY_MODEL
                              /audio 정적 파일 서빙
```

### 환경변수 (.env)
| 변수 | 설명 |
|------|------|
| `YOUTUBE_API_KEY` | YouTube Data API v3 키 |
| `RESEARCH_BASE_DIR` | 수집 영상 저장 루트 (예: `/path/to/research`) |
| `OLLAMA_HOST` | Ollama 서버 주소 |
| `SUMMARY_MODEL` | 요약 LLM 모델명 (기본: gemma4:12b, 빠르게는 qwen3:8b) |
| `SUMMARY_NUM_CTX` | 요약 컨텍스트 토큰 (기본: 16384) |
| `SUMMARY_CHUNK_CHARS` | map 청크 크기 문자수 (기본: 4000) |
| `STT_MODEL` | Whisper 모델 크기 (tiny/base/small/medium/large-v2/large-v3) |
| `STT_DEVICE` | 연산 장치 (auto/cpu/cuda) |
| `AUDIO_DIR` | MP3 저장 폴더 |
| `AUDIO_BASE_URL` | MP3 외부 접근 URL |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 기본 수신 Chat ID |

---

## API 엔드포인트 목록

### 🔹 기본

#### `GET /`
헬스 체크. 서버 상태 확인.
```json
{"status": "ok", "server": "Content Tools API"}
```

---

### 🔹 YouTube 채널 조회

#### `GET /youtube/channel-videos`
yt-dlp로 특정 채널의 영상 목록과 메타데이터를 수집한다. (API 키 불필요)

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `channel_url` | string | ✅ | 채널 URL |
| `limit` | int | ❌ | 최대 수집 수 (기본: 전체) |

**활용**: 내 채널 영상 목록 확인, 특정 채널 분석

---

#### `GET /youtube/my-channel`
사전 설정된 내 채널(`MY_CHANNEL_URL`) 영상 목록 조회. `channel_url` 없이 호출 가능.

| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| `limit` | int | ❌ | 최대 수집 수 |

---

### 🔹 콘텐츠 리서치 (자동 수집)

#### `GET /research/recent-videos`
YouTube Data API로 모니터링 채널의 최근 N일 영상을 수집한다.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `channel_urls` | string | ✅ | 쉼표 구분 채널 URL 목록 |
| `days` | int | 7 | 최근 몇 일 이내 |
| `limit_per_channel` | int | 10 | 채널당 최대 수집 수 |
| `exclude_ids` | string | "" | 제외할 영상 ID (쉼표 구분) |
| `include_subtitles` | bool | false | 자막 추출 여부 |
| `include_summary` | bool | false | Ollama 요약 여부 |

**n8n 연동**: `node-fetch` → 채널 MD 파일 저장 자동화

---

#### `GET /research/keyword-videos`
키워드로 YouTube를 검색해 조회수 높은 최신 영상을 수집한다.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `keywords` | string | ✅ | 쉼표 구분 키워드 목록 |
| `days` | int | 30 | 최근 N일 이내 |
| `max_results` | int | 5 | 키워드당 최대 수집 수 |
| `exclude_ids` | string | "" | 제외할 영상 ID |
| `include_subtitles` | bool | false | 자막 추출 여부 |
| `include_summary` | bool | false | Ollama 요약 여부 |

**n8n 연동**: `node-keyword-fetch` → 키워드 MD 파일 저장 자동화

---

#### `GET /research/collect-keywords-async`
키워드 수집을 백그라운드로 실행. 완료 후 `RESEARCH_BASE_DIR/키워드/YYYY-MM-DD.md`에 저장.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `keywords` | string | ✅ | 쉼표 구분 키워드 목록 |
| `days` | int | 30 | 최근 N일 이내 |
| `max_results` | int | 5 | 키워드당 최대 수집 수 |

---

### 🔹 STT + AI 요약 파이프라인

#### `GET /pipeline/batch-stt-async` ⭐ 핵심
영상 URL 목록을 받아 백그라운드에서 STT → Ollama 요약 → MD 파일에 삽입까지 자동으로 처리.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `video_urls` | string | ✅ | 처리할 YouTube URL (쉼표 구분) |
| `date` | string | ✅ | 날짜 (YYYY-MM-DD) |
| `folder` | string | "채널" | 저장 폴더 (`채널` 또는 `키워드`) |

- Whisper 모델/디바이스는 `STT_MODEL`, `STT_DEVICE` 환경변수로 제어 (n8n 파라미터 불필요)
- 처리 결과: `RESEARCH_BASE_DIR/{folder}/{date}.md`에 요약 + MP3 링크 삽입
- 빈 `video_urls` 전달 시 400 오류 없이 `{"status": "skipped"}` 반환

**n8n 연동**: `node-batch-stt` (채널), `node-keyword-stt` (키워드)

---

#### `GET /pipeline/video`
단일 YouTube 영상을 STT → 요약 → .md 저장 후 Telegram으로 결과 전송.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `url` | string | ✅ | YouTube 영상 URL |
| `language` | string | null | 언어 코드 (미지정 시 자동 감지) |
| `chat_id` | string | null | Telegram Chat ID (미지정 시 환경변수) |

**활용**: 특정 영상 즉시 요약 + 텔레그램 수신 (Telegram Bot 연동)

---

#### `POST /transcribe`
YouTube 오디오를 Whisper로 변환해 텍스트 파일로 저장.

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `url` | string | YouTube URL |
| `model` | string | Whisper 모델 (tiny/small/medium/large-v3) |
| `language` | string | 언어 코드 |
| `keep_audio` | bool | 오디오 파일 보관 여부 |
| `output_dir` | string | 저장 경로 |

---

### 🔹 리포트 & 알림

#### `GET /report/daily` ⭐ 핵심
채널·키워드 수집 MD 파일을 모바일 친화적 HTML로 렌더링. Telegram 인앱 브라우저에서 바로 열림.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `date` | string | 오늘 | 조회 날짜 (YYYY-MM-DD) |
| `folder` | string | "all" | `채널` / `키워드` / `all` |

- `all`: 채널 + 키워드 MD를 합쳐서 렌더링
- 다크 테마, 테이블, blockquote AI 요약 스타일 적용

---

#### `GET /report/notify-daily` ⭐ 핵심
오늘의 수집 결과 요약을 Telegram으로 전송. 리포트 HTML 링크 포함.

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `date` | string | 오늘 | 알림 대상 날짜 (YYYY-MM-DD) |
| `chat_id` | string | 환경변수 | Telegram Chat ID |

전송 메시지 형식:
```
📊 YYYY-MM-DD 콘텐츠 수집 완료

📺 채널 영상: N개
🔍 키워드 영상: N개
📦 총합: N개

📄 리포트 보기 [링크]
```

**n8n 연동**: `node-notify` (Merge + Limit 이후 최종 단계)

---

### 🔹 정적 파일

#### `GET /audio/{filename}`
STT 처리 시 생성된 MP3 파일을 직접 스트리밍. MD 리포트 내 MP3 다운로드 링크에 사용.

---

## n8n 워크플로우 구조 (예시)

```
스케줄 (매일 08:00)
  └→ node-prepare (채널 목록 파싱 + 기존 수집 ID 수집)
        ├→ [채널 브랜치]
        │    node-fetch (recent-videos API)
        │    → node-save (채널 MD 저장)
        │    → node-batch-stt (batch-stt-async, folder=채널)
        │
        └→ [키워드 브랜치]
             node-keyword-fetch (keyword-videos API)
             → node-keyword-save (키워드 MD 저장)
             → node-keyword-stt (batch-stt-async, folder=키워드)

양 브랜치 완료 후:
  → node-merge (Append 모드)
  → node-limit (1개만 통과 — 중복 알림 방지)
  → node-notify (notify-daily API → Telegram 전송)
```

---

## 모니터링 설정 파일

| 파일 | 역할 |
|------|------|
| `콘텐츠 리서치/모니터링 채널 목록.md` | n8n이 읽는 채널 URL 목록. `활성` 컬럼 ✅/❌ 로 수집 여부 제어 |
| `콘텐츠 리서치/모니터링 키워드 목록.md` | n8n이 읽는 키워드 목록. `활성` 컬럼 ✅/❌ 로 수집 여부 제어 |

---

## 향후 활용 아이디어

- **종목별 알림 분기**: 특정 키워드(종목명)가 AI 요약에 포함되면 별도 Telegram 채널로 발송
- **Notion 연동**: 수집된 영상 요약을 Notion 데이터베이스에 자동 추가
- **감성 분석**: Ollama로 영상 요약의 긍정/부정 톤 분류 후 투자 참고
- **다른 주제 적용**: 키워드만 바꾸면 IT 뉴스, 부동산, 해외 주식 등 어떤 주제든 동일하게 적용 가능
- **Shorts 필터**: 60초 미만 영상 자동 제외 (이미 duration_sec 필드 수집 중)
