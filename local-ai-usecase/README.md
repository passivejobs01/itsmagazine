# Local AI Use Case — 텔레그램 봇으로 3가지 자동화

집 서버의 **로컬 AI(자체 호스팅 GPU)** 만으로, **텔레그램 봇 하나**에서 세 가지를 자동 처리하는 활용 사례입니다.
외부 AI API 없이 **STT · LLM · TTS**가 전부 내 서버에서 돌아갑니다.

## 텔레그램으로 하는 3가지
1. **블로그 자동발행** — 유튜브 링크 → 글 생성(LLM) → WordPress 초안 자동 등록
2. **유튜브 영상 요약** — 유튜브 링크 → STT → 요약(LLM) → 모바일 친화 HTML
3. **음성 클로닝(TTS)** — 텍스트 → 등록해 둔 목소리로 합성

> 텔레그램 **그룹 + Topics(주제)** 를 만들면, 각 토픽에 링크나 텍스트만 보내도 해당 기능이 실행됩니다.

## 동작 구조 — FastAPI = 외부에서 내부 AI로 들어가는 "입구"
```
[🌐 외부: 폰·다른 PC] ──요청──▶ [🚪 FastAPI 서버 :8003 (항상 실행)] ──전달──▶ [🧠 내부 로컬 AI: STT·LLM·TTS]
```
FastAPI 서버가 떠 있으면, 외부(폰·다른 PC)에서 **언제든** 내부 로컬 AI에 접근할 수 있습니다.
텔레그램 메시지가 이 입구(`:8003`)로 들어와, 주제·키워드에 따라 알맞은 기능으로 라우팅됩니다.

## 스택 (전부 로컬)
- **STT**: faster-whisper (CTranslate2, GPU)
- **LLM(요약·블로그)**: Ollama (예: `gemma4:12b`, `qwen3:8b`)
- **TTS**: Qwen3-TTS (제로샷 음성 클로닝)
- **서버/조작**: FastAPI + Telegram Bot (Webhook)
- **단일 GPU 시분할**: 작업 전환 시 모델을 자동 언로드해 VRAM 충돌(부분 CPU 오프로드) 방지

## 구성 파일
| 파일 | 역할 |
|---|---|
| `fastapi_linux.py` | 메인 서버 — 3기능 + 텔레그램 토픽 라우팅 |
| `wp_publish.py` | 블로그 발행 헬퍼(글 프롬프트·임베드·중복관리) |
| `video_pipeline.py` | 다운로드 → STT → 요약 → .md 저장 파이프라인 |
| `summarize_transcript.py` | 긴 자막 map-reduce 요약 엔진 (CLI) |
| `resume_summary.py` | 중단된 요약 이어서 생성 |
| `voice_clone.py` | Qwen3-TTS 음성 등록/합성 (CLI) |
| `youtube_transcriber.py` | 단독 STT(자막 추출) CLI |
| `youtube_channel_list.py` | 채널 영상 목록 수집 CLI |
| `subtitle_gen.py` | 자막(SRT) 생성기 |
| `tts_tester.html` | TTS API 로컬 테스터(브라우저) |
| `test_ollama.py`, `test_api.py` | 연결/엔드포인트 테스트 |
| `API_문서.md` | API 엔드포인트 문서 |
| `.env.example` | 환경설정 예시(복사해서 `.env`로) |
| `fastapi_linux.service` | systemd 서비스 유닛(부팅 시 자동 실행) — 경로는 환경에 맞게 수정 |

## 설치

> 이 저장소의 도구들은 **홈 공용 venv 하나(`~/.venv`)** 를 함께 씁니다.
> 도구마다 venv를 따로 만들지 말고, 저장소 **루트의 `setup_cuda_env.sh`** 로 한 번에 셋업하세요.

### 1) Python + 시스템 의존성
Python **3.10+** 필요. ffmpeg도 있어야 합니다.
```bash
# Ubuntu/Debian (미설치 시)
sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg
python3 --version            # 3.10 이상 확인
```

### 2) 공용 venv + 의존성 (한 번에)
저장소 루트에서 실행하면 **`~/.venv` 생성 + CUDA PyTorch + 각 도구 requirements** 까지 처리합니다.
```bash
cd ..                  # 저장소 루트로 (setup_cuda_env.sh 위치)
bash setup_cuda_env.sh
source ~/.venv/bin/activate
```
> 수동으로 하려면: `python3 -m venv ~/.venv && source ~/.venv/bin/activate`
> → `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126`
> → `pip install -r local-ai-usecase/requirements.txt`

### 3) 환경설정 & 로컬 AI
```bash
cp .env.example .env                 # 값 채우기 (토큰/도메인 등)
# Ollama 설치(https://ollama.com) 후 모델 받기
ollama pull gemma4:12b               # 요약/블로그용 LLM
```

### 4) 실행 (임시 테스트)
```bash
source ~/.venv/bin/activate          # 공용 venv 활성화
python fastapi_linux.py
```
**검증**: 브라우저 `http://<서버IP>:8003/docs`(API 문서) · `/player`(자막 플레이어), 또는
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://<서버IP>:8003/docs   # 200 이면 성공
```

## 상시 실행 (systemd) — 켜두면 끝

직접 실행은 터미널을 닫거나 재부팅하면 꺼집니다. `fastapi_linux.service` 로 등록하면
**부팅 시 자동 기동 + 죽으면 자동 재시작**됩니다(로그는 `journalctl`).
```bash
sudo cp fastapi_linux.service /etc/systemd/system/   # 먼저 파일 안의 경로를 내 환경에 맞게 수정
sudo systemctl daemon-reload
sudo systemctl enable --now fastapi_linux             # 등록 + 즉시 시작
systemctl status fastapi_linux                        # active 확인
journalctl -u fastapi_linux -f                        # 실시간 로그
```
> ⚠️ `fastapi_linux.service` 의 `User` · `WorkingDirectory` · `ExecStart`(venv 파이썬·앱 경로)를
> **자기 서버 경로**로 바꿔야 합니다. (이 저장소 예시는 `~/.venv` + `~/itsmagazine/local-ai-usecase` 기준)

## 텔레그램 연동 — 주제(Topic)별 자동 라우팅

텔레그램 **그룹 + Topics(주제)** 를 만들어, 각 주제에 메시지만 보내면 해당 기능이 자동 실행됩니다.
> ⚠️ 봇이 메시지를 받으려면 **공개 HTTPS 웹훅**이 필요합니다 → `.env` 의 `FASTAPI_PUBLIC_URL` 이
> 공개 도메인이어야 합니다(순수 로컬 IP로는 텔레그램이 서버에 도달 못 함). 서버 시작 시 자동 등록됨.

### 1) 봇 · 그룹 준비
- @BotFather 로 봇 생성 → 토큰을 `.env` 의 `TELEGRAM_BOT_TOKEN` 에 입력.
- @BotFather `/setprivacy` → **Disable** (그래야 그룹의 일반 메시지를 모두 수신).
- 그룹 생성 → 설정에서 **Topics(주제) 켜기** → 봇을 **관리자**로 추가 → 주제 4개 생성(📺요약 / 📰블로그 / 🎙음성 / 📝자막).

### 2) 각 주제의 thread_id 확인 → `TG_TOPIC_*` 매핑
서버가 받는 모든 메시지는 로그에 `thread_id` 가 찍힙니다. 이것으로 주제 번호를 알아냅니다.
```bash
journalctl -u fastapi_linux -f | grep Telegram
# 각 주제에 아무 메시지(예: test)를 하나씩 보내면:
# [Telegram] chat_id=-1001234567890 thread_id=2 text='test'
```
- `chat_id`(-100…) → `.env` 의 `TELEGRAM_CHAT_ID`
- 각 주제의 `thread_id` → 해당 `TG_TOPIC_*`
```ini
TELEGRAM_CHAT_ID=-1001234567890
TG_TOPIC_SUMMARY=2     # 📺 영상요약
TG_TOPIC_BLOG=4        # 📰 블로그
TG_TOPIC_TTS=6         # 🎙 음성합성
TG_TOPIC_SUBTITLE=8    # 📝 자막
```
> 값(작은 정수, 비밀 아님) 입력 후 서버 재시작. 일반(General) 영역 메시지는 thread_id 가 없어 '요약'으로 처리됩니다.

### 3) 라우팅 규칙
| 주제 / 키워드 | 보내는 것 | 동작 |
|---|---|---|
| 📺 요약(`TG_TOPIC_SUMMARY`) **또는 일반(기본)** | YouTube URL | STT → 요약 |
| 📰 블로그(`TG_TOPIC_BLOG`) 또는 `블로그`/`blog` | YouTube URL | WordPress 초안 발행 |
| 🎙 음성(`TG_TOPIC_TTS`) | 텍스트(URL 불필요) | 등록한 목소리로 음성 합성 |
| 📝 자막(`TG_TOPIC_SUBTITLE`) 또는 `자막`/`subtitle`/`srt` | YouTube URL | STT → 번역 → 한글 `.srt` |
| (아무 주제) `mp3` | YouTube URL | MP3 링크만 |

> 주제를 안 정해도 **키워드로 동작**합니다(블로그/자막/mp3). 주제를 매핑하면 키워드 없이 URL만 보내도 됩니다.
> 단 **🎙 음성합성만은 `TG_TOPIC_TTS` 지정이 사실상 필수**(키워드 폴백이 없어, 미설정 시 '요약'으로 처리됨).

## 보안 / 주의
- **비밀값은 전부 `.env`로 분리** — `.env`·음성 샘플·생성 데이터는 커밋하지 않습니다(`.gitignore`).
- WordPress 인증은 로그인 비번이 아니라 **Application Password**를 사용합니다.

> 콘텐츠용으로 정리한 데모 코드입니다. 환경에 맞게 `.env`만 채우면 동작합니다.
