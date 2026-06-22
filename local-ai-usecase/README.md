# Local AI Use Case — 텔레그램 봇으로 3가지 자동화

집 서버의 **로컬 AI(자체 호스팅 GPU)** 만으로, **텔레그램 봇 하나**에서 세 가지를 자동 처리하는 활용 사례입니다.
외부 AI API 없이 **STT · LLM · TTS**가 전부 내 서버에서 돌아갑니다.

## 텔레그램으로 하는 3가지
1. **블로그 자동발행** — 유튜브 링크 → 글 생성(LLM) → WordPress 초안 자동 등록
2. **유튜브 영상 요약** — 유튜브 링크 → STT → 요약(LLM) → 모바일 친화 HTML
3. **음성 클로닝(TTS)** — 텍스트 → 등록해 둔 목소리로 합성

> 텔레그램 **그룹 + Topics(주제)** 를 만들면, 각 토픽에 링크나 텍스트만 보내도 해당 기능이 실행됩니다.

## 스택 (전부 로컬)
- **STT**: faster-whisper (CTranslate2, GPU)
- **LLM(요약·블로그)**: Ollama (예: `gemma4:12b`, `qwen3:8b`)
- **TTS**: Qwen3-TTS (제로샷 음성 클로닝)
- **서버/조작**: FastAPI + Telegram Bot (Webhook 또는 long-polling)
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

### 4) 실행
```bash
source ~/.venv/bin/activate          # 공용 venv 활성화
python fastapi_linux.py
# 또는 systemd 서비스로 등록해 상시 실행
#   ExecStart=/home/<user>/.venv/bin/python /home/<user>/itsmagazine/local-ai-usecase/fastapi_linux.py
```

## 텔레그램 사용
- **그룹 + Topics** 구성 → 각 토픽에 링크/텍스트만:
  - 📰 블로그 토픽: 유튜브 링크 → WordPress 초안
  - 📺 요약 토픽: 유튜브 링크 → HTML 요약
  - 🎙 음성 토픽: 텍스트 → 합성 오디오
- 또는 키워드: 링크와 함께 `블로그` / `mp3`
- 봇 프라이버시(@BotFather `/setprivacy`)는 **Disable** 후 그룹 재추가해야 일반 메시지를 받습니다.

## 보안 / 주의
- **비밀값은 전부 `.env`로 분리** — `.env`·음성 샘플·생성 데이터는 커밋하지 않습니다(`.gitignore`).
- 텔레그램 **웹훅은 공개 HTTPS URL**이 필요합니다. 순수 로컬이면 long-polling으로 전환 가능.
- WordPress 인증은 로그인 비번이 아니라 **Application Password**를 사용합니다.

> 콘텐츠용으로 정리한 데모 코드입니다. 환경에 맞게 `.env`만 채우면 동작합니다.
