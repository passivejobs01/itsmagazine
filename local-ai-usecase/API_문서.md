# Content Tools API 문서

> FastAPI 서버 (포트 8003). 텔레그램 봇 + 로컬 AI로 **영상 요약 · 블로그 발행 · 음성 클로닝** 3가지를 처리.
> 공개 주소는 환경에 맞게 설정(`FASTAPI_PUBLIC_URL`).

---

## 시스템 구성

```
[Telegram 봇]  ──webhook──>  [FastAPI 서버 :8003]  ──>  [로컬 AI]
 그룹+Topics                  STT · 요약 · 블로그 · TTS      Ollama(LLM)
                             /audio · /tts_audio 서빙        faster-whisper(STT)
                                                            Qwen3-TTS(음성)
```

### 주요 환경변수 (.env)
| 변수 | 설명 |
|------|------|
| `FASTAPI_PUBLIC_URL` | 공개 URL (웹훅·링크 파생). 로컬만이면 `http://localhost:8003` |
| `OLLAMA_HOST` | Ollama 서버 주소 |
| `SUMMARY_MODEL` | 요약/블로그 LLM (기본 gemma4:12b) · `SUMMARY_NUM_CTX` · `SUMMARY_CHUNK_CHARS` |
| `STT_MODEL` / `STT_DEVICE` | Whisper 모델 / 장치(auto·cpu·cuda) |
| `AUDIO_DIR` | 오디오 저장 폴더 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 봇 토큰 / 기본 수신 chat_id |
| `TG_TOPIC_SUMMARY`·`TG_TOPIC_BLOG`·`TG_TOPIC_TTS` | 텔레그램 토픽 thread_id 매핑 |
| `TTS_DEFAULT_VOICE` | 텔레그램 TTS 기본 음성 |
| `WP_URL`·`WP_USER`·`WP_APP_PASSWORD`·`WP_DEFAULT_STATUS` | WordPress 발행(Application Password) |

---

## 엔드포인트

### 🔹 기본
| 메서드/경로 | 설명 |
|---|---|
| `GET /` | 헬스 체크 |

### 📺 영상 요약
| 메서드/경로 | 설명 |
|---|---|
| `GET /pipeline/video?url=&language=&chat_id=` | YouTube 영상 → STT → 요약(LLM) → `.md` 저장 → Telegram 전송 |
| `GET /summary/view?file=<파일명.md>` | 저장된 요약 `.md`를 모바일 친화 HTML로 렌더 |
| `GET /audio/{filename}` | 생성된 MP3 정적 서빙 |

### 📰 블로그 발행
| 메서드/경로 | 설명 |
|---|---|
| `POST /publish/youtube?url=&status=&chat_id=` | YouTube 영상 → 블로그 글(LLM) → WordPress 초안 발행(`status=publish`면 즉시 발행) |

### 🎙 음성 클로닝 (TTS)
| 메서드/경로 | 설명 |
|---|---|
| `GET /tts/voices` | 등록된 음성 목록 |
| `POST /tts/voices/register` | 음성 등록(참조 오디오 업로드 + ref_text 또는 auto STT) |
| `DELETE /tts/voices/{name}` | 등록 음성 삭제 |
| `POST /tts/generate` | 등록 음성으로 텍스트 합성 → 오디오 URL 반환 (body: voice·text·language·max_chars·chunk_gap·fmt·no_cache) |
| `GET /tts_audio/{filename}` | 합성 오디오 정적 서빙 |

### 💬 Telegram
| 메서드/경로 | 설명 |
|---|---|
| `POST /pipeline/telegram` | 텔레그램 웹훅 수신 → 토픽/키워드로 요약·블로그·TTS·MP3 라우팅 |
| `GET /telegram/webhook-info` | 현재 웹훅 정보 조회 |
| `GET /telegram/register-webhook` | 웹훅 수동 재등록 |

---

## 텔레그램 사용 (토픽 라우팅)
**그룹 + Topics(주제)** 를 만들고 각 토픽 thread_id를 `TG_TOPIC_*`에 매핑하면, 토픽별로 자동 동작:

| 토픽 | 보내면 | 결과 |
|---|---|---|
| 📺 영상 요약 | 유튜브 링크 | STT+요약 → HTML 요약 링크 |
| 📰 블로그 발행 | 유튜브 링크 | 블로그 글 → WordPress 초안 |
| 🎙 음성 클로닝 | 텍스트 | 기본 음성으로 합성 → 오디오 |

- 일반 토픽 폴백: 링크 + 키워드 `블로그` / `mp3`
- 봇 프라이버시(@BotFather `/setprivacy`)는 **Disable** 후 그룹 재추가해야 일반 메시지를 받음
- 동시 요청은 `_pipeline_lock`으로 순차 처리(단일 GPU 보호), 작업 전환 시 모델 자동 언로드

---

## GPU 시분할
단일 GPU에서 whisper(STT)·Ollama(LLM)·Qwen-TTS가 충돌 없이 번갈아 사용:
- STT 후 whisper 해제 → 요약 LLM 풀 로드
- 요약/합성 전환 시 이전 모델 언로드 → 부분 CPU 오프로드 방지
