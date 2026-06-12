# =============================================================================
# 자주 쓰는 systemd 관리 명령 (서비스명: fastapi_linux)
# -----------------------------------------------------------------------------
#   sudo systemctl restart fastapi_linux    # 재시작 (코드 수정 후)
#   sudo systemctl stop    fastapi_linux    # 중지
#   sudo systemctl disable fastapi_linux    # 부팅 자동실행 해제
#   journalctl -u fastapi_linux -f          # 로그 보기 (실시간)
# =============================================================================

import asyncio
import html
import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

# 플랫폼별 환경파일 자동 선택: Windows → .env_windows, 그 외(Linux/macOS) → .env_linux
# 선택된 파일이 없으면 기본 .env 로 폴백
_env_dir = Path(__file__).resolve().parent
_platform_env = _env_dir / (".env_windows" if os.name == "nt" else ".env_linux")
_env_file = _platform_env if _platform_env.exists() else _env_dir / ".env"
load_dotenv(_env_file)

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# video_pipeline 모듈을 단일 소스로 재사용 (오디오 준비·STT·요약·파일명·프롬프트).
# faster-whisper는 video_pipeline 내부에서 지연 import하므로 미설치여도 import는 성공한다.
try:
    from video_pipeline import (
        build_summary_prompt,
        free_whisper_models,
        prepare_audio,
        sanitize_filename,
        save_markdown,
        transcribe,
    )
    _PIPELINE_AVAILABLE = True
except ImportError:
    _PIPELINE_AVAILABLE = False

# 요약 엔진(map-reduce) — summarize_transcript의 청킹·프롬프트·후처리를 단일 소스로 재사용.
# 전송 계층만 fastapi의 async httpx + 서버 OLLAMA_HOST 로 바꿔 사용한다.
try:
    from summarize_transcript import (
        chunk_text as _chunk_text,
        MAP_PROMPT as _MAP_PROMPT,
        PROMPTS as _SUMMARY_PROMPTS,
        normalize_md as _normalize_md,
        strip_think as _strip_think,
    )
    _SUMMARIZER_AVAILABLE = True
except ImportError:
    _SUMMARIZER_AVAILABLE = False

# WordPress 발행 헬퍼 (블로그 프롬프트·파서·임베드·중복관리)
try:
    import wp_publish as _wp
    _WP_AVAILABLE = True
except ImportError:
    _wp = None
    _WP_AVAILABLE = False


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8003"))

# Ollama (로컬 LLM 요약) — STT·요약 모두 서버 로컬(ubuntu) 리소스 사용. 요약 모델은 SUMMARY_MODEL.
OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# 요약(map-reduce) 설정 — 품질 우선 기본값 gemma4:12b. 빠르게는 SUMMARY_MODEL=qwen3:8b.
SUMMARY_MODEL       = os.getenv("SUMMARY_MODEL", "gemma4:12b")
SUMMARY_NUM_CTX     = int(os.getenv("SUMMARY_NUM_CTX", "16384"))
SUMMARY_CHUNK_CHARS = int(os.getenv("SUMMARY_CHUNK_CHARS", "4000"))

# WordPress 자동 발행 (REST API)
WP_URL            = os.getenv("WP_URL", "https://your-blog.example.com").rstrip("/")
WP_USER           = os.getenv("WP_USER", "")
WP_APP_PASSWORD   = os.getenv("WP_APP_PASSWORD", "")
WP_DEFAULT_STATUS = os.getenv("WP_DEFAULT_STATUS", "draft")   # draft | publish

# Telegram 토픽(Forum) 라우팅 — 각 토픽의 message_thread_id 를 동작에 매핑.
# 슈퍼그룹+Topics 생성 후 각 토픽에 메시지를 보내면 로그에 thread_id가 찍힘 → 여기에 채움.
TG_TOPIC_SUMMARY  = os.getenv("TG_TOPIC_SUMMARY", "")   # 📺 영상 요약 토픽 thread_id
TG_TOPIC_BLOG     = os.getenv("TG_TOPIC_BLOG", "")      # 📰 블로그 발행 토픽 thread_id
TG_TOPIC_TTS      = os.getenv("TG_TOPIC_TTS", "")       # 🎙 음성 클로닝 토픽 thread_id
TTS_DEFAULT_VOICE = os.getenv("TTS_DEFAULT_VOICE", "")  # 텔레그램 TTS 기본 음성(미지정 시 첫 등록 음성)

# 오디오 파일 저장 경로
AUDIO_DIR = os.getenv("AUDIO_DIR", str(Path(__file__).parent / "audio"))

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# 공개 URL — 웹훅·MP3·리포트 링크 모두 이 값에서 파생 (단일 설정)
FASTAPI_PUBLIC_URL = os.getenv("FASTAPI_PUBLIC_URL", "https://localhost:8003").rstrip("/")
AUDIO_BASE_URL     = FASTAPI_PUBLIC_URL  # MP3 외부 접근 URL (별도 설정 불필요)

# Whisper STT 설정 (FastAPI 내부 설정 — n8n에서 파라미터로 받지 않음)
# STT_MODEL: tiny | base | small | medium | large-v2 | large-v3
# STT_DEVICE: auto | cpu | cuda
STT_MODEL  = os.getenv("STT_MODEL",  "small")
STT_DEVICE = os.getenv("STT_DEVICE", "auto")


# ──────────────────────────────────────────────
# 로깅
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("content_tools")


# ──────────────────────────────────────────────
# ThreadPoolExecutor (yt-dlp 동기 작업용)
# ──────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="yt-dlp")

# 영상 파이프라인(STT+요약) 직렬화 락 — 텔레그램으로 여러 링크가 와도 GPU 병렬 없이 순차 처리.
_pipeline_lock = asyncio.Lock()


async def _register_telegram_webhook() -> None:
    """Telegram Bot API에 웹훅 URL을 등록한다."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("[Telegram] BOT_TOKEN 미설정 — 웹훅 등록 건너뜀")
        return
    webhook_url = f"{FASTAPI_PUBLIC_URL.rstrip('/')}/pipeline/telegram"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(api_url, json={"url": webhook_url}, timeout=10)
            data = resp.json()
            if data.get("ok"):
                logger.info(f"[Telegram] 웹훅 등록 완료: {webhook_url}")
            else:
                logger.warning(f"[Telegram] 웹훅 등록 실패: {data.get('description')}")
        except Exception as e:
            logger.warning(f"[Telegram] 웹훅 등록 오류: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("[Server] 콘텐츠 도구 API 서버 시작")
    await _register_telegram_webhook()
    yield
    _executor.shutdown(wait=False)
    logger.info("[Server] 서버 종료")


app = FastAPI(
    title="Content Tools API",
    description="유튜브 운영을 위한 콘텐츠 수집·관리 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 오디오 파일 정적 서빙: GET /audio/파일명.mp3
_audio_dir = Path(AUDIO_DIR)
_audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(_audio_dir)), name="audio")


# ──────────────────────────────────────────────
# TTS (음성 클로닝) — voice_clone.py 기능을 API로 노출
# 로직은 voice_clone 함수를 그대로 재사용(단일 소스). STT·TTS 모두 서버 로컬 GPU 사용.
# ──────────────────────────────────────────────
try:
    import voice_clone as _vc
    _TTS_AVAILABLE = True
except Exception:                       # torch/qwen_tts 미설치 등
    _vc = None
    _TTS_AVAILABLE = False

# TTS 결과 정적 서빙: GET /tts_audio/파일명
_tts_dir = Path(_vc.TTS_OUTPUT_DIR) if _TTS_AVAILABLE else (Path(__file__).parent / "tts_output")
_tts_dir.mkdir(parents=True, exist_ok=True)
app.mount("/tts_audio", StaticFiles(directory=str(_tts_dir)), name="tts_audio")

# TTS는 GPU를 점유하므로 동시 1건만 처리(OOM 방지)
_tts_lock = asyncio.Lock()


class TTSGenerateRequest(BaseModel):
    voice: str                      # 등록된 음성 이름
    text: str                       # 합성할 텍스트
    language: str = "Korean"
    max_chars: int = 200            # 청크당 최대 글자수(0=분할 안 함)
    chunk_gap: float = 0.3          # 청크 사이 무음(초)
    fmt: str = "wav"                # wav | mp3
    no_cache: bool = False          # prompt 디스크 캐시 사용 안 함


def _tts_generate_sync(req: "TTSGenerateRequest") -> dict:
    """블로킹 TTS 합성 (executor에서 실행). 결과를 TTS_OUTPUT_DIR에 저장하고 메타 반환."""
    import time as _t
    start = _t.time()
    profile = _vc.load_voice(req.voice)                       # 미등록이면 FileNotFoundError
    model, device_map = _vc.load_model(_vc.QWEN_TTS_MODEL, "auto", "auto")
    prompt = _vc.get_or_build_prompt(model, profile, device_map, use_cache=not req.no_cache)
    audio, sr = _vc.synthesize(model, req.text, req.language, prompt, req.max_chars, req.chunk_gap)

    ext = ".mp3" if req.fmt.lower() == "mp3" else ".wav"
    ts  = datetime.now().strftime("%m%d_%H%M%S")
    out_path = _tts_dir / f"{sanitize_filename(req.voice)}_{ts}{ext}"
    _vc.save_audio(audio, sr, out_path)
    return {
        "filename":    out_path.name,
        "elapsed_sec": round(_t.time() - start, 1),
        "sample_rate": int(sr),
        "chars":       len(req.text),
    }


@app.get("/tts/voices", summary="등록된 음성 목록")
async def tts_list_voices():
    if not _TTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="TTS 모듈을 사용할 수 없습니다(torch/qwen_tts 확인).")
    voices = _vc.list_voices()
    return {
        "ok": True,
        "count": len(voices),
        "voices": [
            {"name": v["name"], "created": v.get("created", ""),
             "ref_text": (v.get("ref_text", "") or "")[:80]}
            for v in voices
        ],
    }


@app.post("/tts/voices/register", summary="음성 프로필 등록(참조 음성 업로드)")
async def tts_register(
    name: str = Form(..., description="음성 이름(재사용 키)"),
    language: str = Form("Korean", description="참조 텍스트 자동추출 시 언어 힌트"),
    ref_text: str = Form("", description="참조 음성의 정확한 발화 내용(직접 입력)"),
    auto_ref_text: bool = Form(False, description="true면 faster-whisper로 자동 추출"),
    file: UploadFile = File(..., description="참조 음성 파일(3~15초 권장)"),
):
    if not _TTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="TTS 모듈을 사용할 수 없습니다.")

    ext = (Path(file.filename or "ref.wav").suffix or ".wav").lower()
    tmp = _tts_dir / f"_upload_{sanitize_filename(name)}{ext}"
    tmp.write_bytes(await file.read())

    loop = asyncio.get_event_loop()
    try:
        if auto_ref_text:
            async with _tts_lock:        # STT도 GPU 사용 → 직렬화
                ref = await loop.run_in_executor(
                    _executor, _vc.auto_transcribe_ref, str(tmp), language)
        elif ref_text.strip():
            ref = ref_text.strip()
        else:
            raise HTTPException(status_code=400,
                                detail="ref_text를 입력하거나 auto_ref_text=true로 자동 추출하세요.")
        profile = await loop.run_in_executor(_executor, _vc.register_voice, name, str(tmp), ref)
        return {"ok": True, "name": profile["name"], "ref_text": profile["ref_text"][:120]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TTS] 등록 실패: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


@app.delete("/tts/voices/{name}", summary="등록 음성 삭제")
async def tts_remove(name: str):
    if not _TTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="TTS 모듈을 사용할 수 없습니다.")
    base = _vc.VOICES_DIR
    removed = []
    for suffix in [".json", ".prompt.pt"]:
        p = base / f"{name}{suffix}"
        if p.exists():
            p.unlink(); removed.append(p.name)
    for p in base.glob(f"{name}.ref.*"):
        p.unlink(); removed.append(p.name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"등록된 음성이 없습니다: {name}")
    return {"ok": True, "removed": removed}


@app.post("/tts/generate", summary="등록 음성으로 텍스트 합성")
async def tts_generate(req: TTSGenerateRequest):
    if not _TTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="TTS 모듈을 사용할 수 없습니다.")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text가 비어 있습니다.")

    loop = asyncio.get_event_loop()
    try:
        async with _tts_lock:            # GPU 동시 1건
            # TTS 모델 로드 전: 영상 파이프라인 모델(whisper/LLM)이 GPU에 남아있으면 비워
            # VRAM 확보 (Qwen-TTS가 GPU에 온전히 올라가도록). TTS 모델은 캐시 유지 → 반복 합성은 빠름.
            if _PIPELINE_AVAILABLE:
                await loop.run_in_executor(_executor, free_whisper_models)
            async with httpx.AsyncClient() as client:
                await _unload_ollama_model(client, SUMMARY_MODEL)
            meta = await loop.run_in_executor(_executor, _tts_generate_sync, req)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[TTS] 생성 실패: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    url = f"{FASTAPI_PUBLIC_URL}/tts_audio/{quote(meta['filename'])}"
    logger.info(f"[TTS] 생성 완료: {meta['filename']} ({meta['elapsed_sec']}s)")
    return {"ok": True, "url": url, **meta}


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────

async def _ollama_generate(
    client: httpx.AsyncClient, model: str, prompt: str,
    *, num_ctx: int = SUMMARY_NUM_CTX, temperature: float = 0.3, timeout: float = 600.0,
) -> str:
    """Ollama /api/generate 단일 호출 (async). 응답에서 thinking 블록을 제거해 반환."""
    if model.lower().startswith("qwen3") and "/no_think" not in prompt:
        prompt = prompt + " /no_think"  # qwen3 계열 사고과정 출력 비활성
    resp = await client.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.json().get("response", "").strip()
    return _strip_think(text) if _SUMMARIZER_AVAILABLE else text


async def _unload_ollama_model(client: httpx.AsyncClient, model: str) -> None:
    """Ollama 모델을 즉시 언로드(keep_alive=0)해 GPU VRAM을 반환한다.
    단일 GPU에서 다음 STT가 whisper를 GPU에 올릴 수 있도록 요약 직후 호출한다."""
    try:
        await client.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=30.0,
        )
        logger.info(f"[요약] {model} 언로드 — GPU VRAM 반환")
    except Exception as e:
        logger.warning(f"[요약] 모델 언로드 실패(무시): {type(e).__name__}: {e}")


async def _summarize_with_ollama(text: str, client: httpx.AsyncClient, title: str = "",
                                 final_template: Optional[str] = None) -> str:
    """로컬 Ollama로 한국어 결과물을 생성한다 (map-reduce).

    긴 자막도 잘리지 않도록 청크로 나눠 각 청크를 충실히 추출(map)한 뒤
    합쳐서 reduce 단계에서 final_template으로 정리한다(짧은 자막은 1청크 → 단일 호출).
    final_template 미지정 시 '정리 노트' 요약. 블로그 글 등 다른 형식은 템플릿만 바꿔 재사용.
    모델은 SUMMARY_MODEL(기본 gemma4:12b), 서버 로컬 OLLAMA_HOST 사용.
    """
    if not text.strip():
        return ""
    try:
        # 폴백: 요약 엔진 모듈이 없으면 기존 단일 호출(앞부분만)
        if not _SUMMARIZER_AVAILABLE:
            return await _ollama_generate(client, SUMMARY_MODEL, build_summary_prompt(text, title))

        template = final_template or _SUMMARY_PROMPTS["detailed"]
        chunks = _chunk_text(text, SUMMARY_CHUNK_CHARS)
        logger.info(f"[요약] {len(text)}자 → {len(chunks)}청크 (model={SUMMARY_MODEL})")

        if len(chunks) <= 1:
            content = text
        else:
            notes: list[str] = []
            for i, ch in enumerate(chunks, 1):
                note = await _ollama_generate(
                    client, SUMMARY_MODEL,
                    _MAP_PROMPT.format(idx=i, total=len(chunks), chunk=ch),
                )
                logger.info(f"[요약][MAP {i}/{len(chunks)}] {len(note)}자")
                notes.append(f"### 파트 {i}\n{note}")
            content = "\n\n".join(notes)

        final_prompt = template.format(title=title, content=content)
        result = await _ollama_generate(client, SUMMARY_MODEL, final_prompt, timeout=900.0)
        logger.info(f"[요약] 완료 ({len(result)}자)")
        return _normalize_md(result)
    except Exception as e:
        logger.warning(f"[요약] 실패: {type(e).__name__}: {e}")
        return ""


# ──────────────────────────────────────────────
# YouTube Data API v3 수집 로직 (비동기)
# ──────────────────────────────────────────────

@app.get("/", summary="헬스 체크")
async def health_check():
    return {"status": "ok", "server": "Content Tools API", "host": FASTAPI_PUBLIC_URL}


def _summary_teaser(summary: str, limit: int = 160) -> str:
    """요약에서 '한 줄 요약'(또는 첫 의미있는 문장)을 짧은 미리보기로 추출한다."""
    if not summary:
        return ""
    lines = [l.strip() for l in summary.splitlines()]
    cand = ""
    for i, l in enumerate(lines):
        if "한 줄 요약" in l:
            cand = next((lines[j] for j in range(i + 1, len(lines)) if lines[j]), "")
            break
    if not cand:  # 폴백: 헤더/불릿/표 기호 없는 첫 줄
        cand = next((l for l in lines if l and not l.startswith(("#", "-", "*", ">", "|"))), "")
    cand = re.sub(r"[*_`#>]", "", cand).strip()
    return (cand[:limit] + "…") if len(cand) > limit else cand


def _summary_view_url(md_name: str) -> str:
    """저장된 요약 .md 파일명을 HTML 뷰 URL로 변환한다."""
    return f"{FASTAPI_PUBLIC_URL}/summary/view?file={quote(md_name)}"


async def _send_telegram(chat_id: str, text: str, thread_id: Optional[int] = None) -> None:
    """Telegram Bot API로 메시지를 전송한다. thread_id가 있으면 해당 토픽 스레드에 답한다."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("[Telegram] BOT_TOKEN 미설정")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if thread_id:
        payload["message_thread_id"] = thread_id
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=10.0)
        except Exception as e:
            logger.error(f"[Telegram] 전송 실패: {e}")


async def _run_mp3_pipeline(url: str, chat_id: str, thread_id: Optional[int] = None) -> None:
    """백그라운드에서 오디오만 다운로드하고 MP3 다운로드 링크를 Telegram으로 전송한다."""
    audio_dir = Path(AUDIO_DIR)
    loop = asyncio.get_event_loop()
    try:
        logger.info(f"[MP3] 시작: {url}")
        audio_path, metadata = await loop.run_in_executor(
            _executor, prepare_audio, url, audio_dir
        )
        title = metadata.get("title", "video")
        mp3_url = f"{AUDIO_BASE_URL}/audio/{audio_path.name}"
        logger.info(f"[MP3] 완료: {audio_path.name}")

        lines = [
            f"🎵 <b>{title}</b>",
            "",
            f"<a href='{mp3_url}'>⬇️ MP3 다운로드</a>",
        ]
        await _send_telegram(chat_id, "\n".join(lines), thread_id)
    except Exception as e:
        logger.error(f"[MP3] 오류: {type(e).__name__}: {e}")
        await _send_telegram(chat_id, f"❌ MP3 추출 실패\n{type(e).__name__}: {e}", thread_id)


async def _run_video_pipeline(url: str, model: str, language: Optional[str], chat_id: str,
                              thread_id: Optional[int] = None) -> None:
    """백그라운드에서 STT → 요약 → .md 저장 → Telegram 전송을 실행한다.

    _pipeline_lock으로 직렬화 — 여러 링크가 동시에 와도 한 번에 한 영상씩 순차 처리한다
    (faster-whisper STT가 GPU에서 병렬로 도는 것을 방지).
    """
    import time

    audio_dir  = Path(AUDIO_DIR)
    output_dir = Path(__file__).parent / "summaries"

    async with _pipeline_lock:           # 한 번에 한 영상만 (GPU 병렬 방지)
        start = time.time()
        try:
            # 1. 오디오 다운로드
            logger.info(f"[Pipeline] 시작: {url}")
            loop = asyncio.get_event_loop()
            audio_path, metadata = await loop.run_in_executor(
                _executor, prepare_audio, url, audio_dir
            )
            title = metadata.get("title", "video")
            logger.info(f"[Pipeline] 오디오 준비 완료: {audio_path.name}")

            # STT 전: TTS 모델이 GPU에 상주 중이면 내려 VRAM 확보 (영상 처리에 GPU 양보).
            if _TTS_AVAILABLE:
                await loop.run_in_executor(_executor, _vc.free_tts_models)

            # 2. STT
            transcript, detected_lang = await loop.run_in_executor(
                _executor, transcribe, audio_path, model, "auto", language, 4
            )
            logger.info(f"[Pipeline] STT 완료: {len(transcript)}자")

            # STT용 whisper 모델을 GPU에서 내려 VRAM 반환 → 요약(LLM)이 GPU를 온전히 쓰도록.
            # (단일 GPU에서 whisper 6GB + gemma 8GB 동시 상주 불가 → 부분 CPU 오프로드 방지)
            await loop.run_in_executor(_executor, free_whisper_models)

            # 3. Ollama 요약 (map-reduce, 서버 로컬 리소스)
            #    자막 자체가 없으면 STT 실패 → 저장하지 않고 명확히 알림.
            if not transcript:
                logger.warning(f"[Pipeline] 자막 없음(STT 실패): {url}")
                await _send_telegram(chat_id, f"❌ 자막을 추출하지 못했어요 (STT 실패)\n{url}", thread_id)
                return
            async with httpx.AsyncClient() as client:
                # 요약 직전: 이전 작업 때 '부분 적재'된 채 남아있을 수 있는 LLM을 먼저 내린다.
                # (Ollama는 이미 로드된 모델을 자동 재배치하지 않으므로, whisper 해제 후
                #  새로 로딩해야 전체 레이어가 GPU에 올라간다 → CPU 오프로드 방지)
                await _unload_ollama_model(client, SUMMARY_MODEL)
                summary = await _summarize_with_ollama(transcript, client, title)
                # 요약 끝 → gemma 언로드해 다음 영상 STT가 GPU를 확보하도록.
                await _unload_ollama_model(client, SUMMARY_MODEL)

            # 4. .md 저장 (자막은 항상 보존 — 요약이 비어도 저장해 이후 재개 가능).
            #    .md 내 MP3 링크는 공개 URL(AUDIO_BASE_URL=FASTAPI_PUBLIC_URL) 사용 — localhost 방지.
            out_path = save_markdown(
                output_dir, metadata, audio_path,
                transcript, detected_lang, summary, model,
                base_url=AUDIO_BASE_URL,
            )
            logger.info(f"[Pipeline] .md 저장: {out_path} (요약 {len(summary)}자)")

            # 5. Telegram 결과 전송 — 본문 대신 링크. 요약이 비면 '완료'로 위장하지 않고 경고.
            elapsed     = time.time() - start
            mins, sec   = divmod(int(elapsed), 60)
            mp3_url     = f"{AUDIO_BASE_URL}/audio/{audio_path.name}"
            summary_url = _summary_view_url(out_path.name)

            if summary:
                lines = [f"📹 <b>{html.escape(title)}</b>"]
                teaser = _summary_teaser(summary)
                if teaser:
                    lines += ["", f"📌 {html.escape(teaser)}"]
                lines += [
                    "",
                    f"📄 <a href='{summary_url}'>요약 보기</a>",
                    f"🎵 <a href='{mp3_url}'>오디오 듣기</a>",
                    f"⏱ 처리시간: {mins}분 {sec}초",
                ]
            else:
                logger.warning(f"[Pipeline] 요약 실패(빈 결과): {title}")
                lines = [
                    f"📹 <b>{html.escape(title)}</b>",
                    "",
                    "⚠️ 요약 생성에 실패했어요. 자막·오디오는 저장됐습니다.",
                    "",
                    f"📄 <a href='{summary_url}'>자막 보기</a>",
                    f"🎵 <a href='{mp3_url}'>오디오 듣기</a>",
                    f"⏱ 처리시간: {mins}분 {sec}초",
                    "",
                    "🔁 서버에서 resume_summary.py로 요약만 이어서 생성할 수 있어요.",
                ]
            await _send_telegram(chat_id, "\n".join(lines), thread_id)

        except Exception as e:
            logger.error(f"[Pipeline] 오류: {type(e).__name__}: {e}")
            await _send_telegram(chat_id, f"❌ 처리 실패\n{type(e).__name__}: {e}", thread_id)


# ──────────────────────────────────────────────
# WordPress 자동 발행 (유튜브 영상 → 블로그 초안)
# ──────────────────────────────────────────────
async def _wp_create_post(client: httpx.AsyncClient, title: str, content: str,
                          status: str) -> dict:
    """WordPress REST API로 글을 생성한다. Application Password Basic 인증."""
    import base64
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    # ?rest_route= 형식 사용: permalink 구조(Plain)와 무관하게 동작, canonical 301 회피
    resp = await client.post(
        f"{WP_URL}/?rest_route=/wp/v2/posts",
        json={"title": title, "content": content, "status": status},
        headers={"Authorization": f"Basic {token}"},
        timeout=30.0,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json()


async def _run_blog_pipeline(url: str, chat_id: str, status: Optional[str] = None,
                             thread_id: Optional[int] = None) -> None:
    """유튜브 영상 → STT → 블로그 글(LLM) → WordPress 초안 발행 → Telegram 알림.

    GPU 시분할/직렬화는 영상 파이프라인과 동일하게 _pipeline_lock으로 처리.
    """
    import time
    status = status or WP_DEFAULT_STATUS

    if not _WP_AVAILABLE:
        await _send_telegram(chat_id, "⚠️ wp_publish 모듈을 찾을 수 없습니다.", thread_id)
        return
    if not (WP_USER and WP_APP_PASSWORD):
        await _send_telegram(chat_id, "⚠️ WordPress 인증 미설정(WP_USER/WP_APP_PASSWORD).", thread_id)
        return

    video_id = _wp.youtube_id(url)
    # 중복 방지: 이미 발행한 영상이면 기존 링크 안내 후 종료
    if video_id:
        existing = _wp.is_published(video_id)
        if existing:
            edit = f"{WP_URL}/wp-admin/post.php?post={existing['post_id']}&action=edit"
            await _send_telegram(chat_id, f"ℹ️ 이미 발행된 영상입니다.\n📝 <a href='{edit}'>편집</a>", thread_id)
            return

    audio_dir = Path(AUDIO_DIR)
    async with _pipeline_lock:
        start = time.time()
        try:
            logger.info(f"[Blog] 시작: {url}")
            loop = asyncio.get_event_loop()
            audio_path, metadata = await loop.run_in_executor(_executor, prepare_audio, url, audio_dir)
            title = metadata.get("title", "video")

            if _TTS_AVAILABLE:
                await loop.run_in_executor(_executor, _vc.free_tts_models)

            transcript, _lang = await loop.run_in_executor(
                _executor, transcribe, audio_path, STT_MODEL, "auto", None, 4)
            if not transcript:
                await _send_telegram(chat_id, f"❌ 자막을 추출하지 못했어요 (STT 실패)\n{url}", thread_id)
                return
            logger.info(f"[Blog] STT 완료: {len(transcript)}자")

            # 블로그 글 생성 (map-reduce + 블로그 reduce 템플릿)
            async with httpx.AsyncClient() as client:
                await _unload_ollama_model(client, SUMMARY_MODEL)
                article_md = await _summarize_with_ollama(
                    transcript, client, title, final_template=_wp.BLOG_TEMPLATE)
                await _unload_ollama_model(client, SUMMARY_MODEL)

                if not article_md.strip():
                    await _send_telegram(chat_id, f"❌ 블로그 글 생성 실패\n{title}", thread_id)
                    return

                post_title, body_md, tags = _wp.parse_blog_markdown(article_md)
                body_html = _wp.md_to_html(body_md)
                content = _wp.build_post_html(url, body_html)

                # WordPress 초안 생성
                post = await _wp_create_post(client, post_title, content, status)

            post_id = post.get("id")
            link = post.get("link", "")
            if video_id and post_id:
                _wp.mark_published(video_id, post_id, link)

            elapsed = int(time.time() - start)
            mins, sec = divmod(elapsed, 60)
            edit_url = f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit"
            preview = f"{WP_URL}/?p={post_id}&preview=true"
            status_ko = "발행됨" if status == "publish" else "초안 생성됨"
            await _send_telegram(chat_id, "\n".join([
                f"📰 <b>{html.escape(post_title)}</b>",
                f"WordPress {status_ko} · 태그: {html.escape(', '.join(tags) if tags else '-')}",
                "",
                f"✏️ <a href='{edit_url}'>편집하기</a>",
                f"👁 <a href='{preview}'>미리보기</a>",
                f"⏱ 처리시간: {mins}분 {sec}초",
            ]), thread_id)
            logger.info(f"[Blog] 완료: post_id={post_id} ({status})")

        except Exception as e:
            logger.error(f"[Blog] 오류: {type(e).__name__}: {e}")
            await _send_telegram(chat_id, f"❌ 블로그 발행 실패\n{type(e).__name__}: {e}", thread_id)


async def _send_telegram_audio(chat_id: str, audio_url: str, caption: str,
                               thread_id: Optional[int] = None) -> None:
    """Telegram sendAudio로 오디오 전송(URL). 실패하면 링크 메시지로 폴백."""
    if not TELEGRAM_BOT_TOKEN:
        return
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
    payload = {"chat_id": chat_id, "audio": audio_url, "caption": caption}
    if thread_id:
        payload["message_thread_id"] = thread_id
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(api, json=payload, timeout=30.0)
            if r.status_code == 200:
                return
        except Exception as e:
            logger.warning(f"[Telegram] sendAudio 실패→링크 폴백: {e}")
    await _send_telegram(chat_id, f"{caption}\n🎵 <a href='{audio_url}'>듣기</a>", thread_id)


async def _run_tts_telegram(text: str, chat_id: str, thread_id: Optional[int] = None) -> None:
    """텔레그램 TTS 토픽: 텍스트 → 등록 음성으로 합성 → 오디오 전송."""
    if not _TTS_AVAILABLE:
        await _send_telegram(chat_id, "⚠️ TTS 모듈을 사용할 수 없습니다.", thread_id)
        return
    voice = TTS_DEFAULT_VOICE
    if not voice:
        voices = _vc.list_voices()
        if not voices:
            await _send_telegram(chat_id, "⚠️ 등록된 음성이 없습니다. 먼저 음성을 등록하세요.", thread_id)
            return
        voice = voices[0]["name"]

    req = TTSGenerateRequest(voice=voice, text=text, fmt="mp3")
    loop = asyncio.get_event_loop()
    try:
        async with _tts_lock:
            if _PIPELINE_AVAILABLE:
                await loop.run_in_executor(_executor, free_whisper_models)
            async with httpx.AsyncClient() as client:
                await _unload_ollama_model(client, SUMMARY_MODEL)
            meta = await loop.run_in_executor(_executor, _tts_generate_sync, req)
    except FileNotFoundError as e:
        await _send_telegram(chat_id, f"❌ 음성 '{voice}' 없음\n{e}", thread_id)
        return
    except Exception as e:
        logger.error(f"[TTS] 텔레그램 합성 실패: {type(e).__name__}: {e}")
        await _send_telegram(chat_id, f"❌ 합성 실패\n{type(e).__name__}: {e}", thread_id)
        return

    audio_url = f"{FASTAPI_PUBLIC_URL}/tts_audio/{quote(meta['filename'])}"
    await _send_telegram_audio(
        chat_id, audio_url,
        f"🎙 {voice} · {meta['chars']}자 · {meta['elapsed_sec']}s", thread_id)


@app.get("/pipeline/video", summary="YouTube 영상 STT + 요약 (비동기)")
async def pipeline_video(
    background_tasks: BackgroundTasks,
    url:      str           = Query(...,         description="YouTube 영상 URL"),
    language: Optional[str] = Query(default=None, description="언어 코드 (미지정 시 자동 감지)"),
    chat_id:  Optional[str] = Query(default=None, description="Telegram Chat ID (미지정 시 환경변수 사용)"),
):
    """
    YouTube URL을 받아 백그라운드에서 STT → Ollama 요약 → .md 저장을 수행하고
    완료 시 Telegram으로 결과를 전송한다.
    Whisper 모델/디바이스는 STT_MODEL, STT_DEVICE 환경변수로 제어한다.
    """
    if not _PIPELINE_AVAILABLE:
        raise HTTPException(status_code=500, detail="video_pipeline.py를 찾을 수 없습니다.")

    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not target_chat_id:
        raise HTTPException(status_code=400, detail="chat_id가 필요합니다.")

    background_tasks.add_task(
        _run_video_pipeline, url, STT_MODEL, language, target_chat_id
    )

    # 즉시 "처리 중" 메시지 전송
    await _send_telegram(target_chat_id, f"⏳ 처리 시작\n{url}\n\n모델: {STT_MODEL}\n완료되면 알려드릴게요!")

    return {
        "status":  "started",
        "url":     url,
        "model":   STT_MODEL,
        "device":  STT_DEVICE,
        "chat_id": target_chat_id,
        "message": "백그라운드에서 처리 중입니다. 완료 시 Telegram으로 결과를 전송합니다.",
    }


# ──────────────────────────────────────────────
# Telegram 웹훅 — YouTube URL 처리
# ──────────────────────────────────────────────

_YT_URL_PATTERN = re.compile(
    r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]{11}'
)


@app.post("/pipeline/telegram", summary="Telegram 메시지 수신 → YouTube 파이프라인 실행")
async def pipeline_telegram(request: Request, background_tasks: BackgroundTasks):
    """
    n8n Telegram Trigger가 전달하는 업데이트를 수신한다.
    - YouTube URL이 포함된 메시지 → STT + 요약 파이프라인 실행
    - YouTube URL이 없는 메시지  → 안내 메시지 전송
    Telegram 봇 토큰은 환경변수(TELEGRAM_BOT_TOKEN)만 사용하므로 n8n에 노출되지 않는다.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "detail": "invalid json"}

    # Telegram update 구조에서 메시지 추출
    message   = body.get("message") or body.get("edited_message") or {}
    text      = message.get("text", "")
    chat_id   = str(message.get("chat", {}).get("id", ""))
    thread_id = message.get("message_thread_id")     # 토픽(Forum) 스레드 ID (없으면 None)

    if not chat_id:
        return {"ok": False, "detail": "chat_id 없음"}

    # 토픽 매핑 디스커버리용 로그 — 토픽에 메시지를 보내면 여기서 thread_id 확인 가능
    logger.info(f"[Telegram] chat_id={chat_id} thread_id={thread_id} text={text[:60]!r}")
    tid = str(thread_id) if thread_id else ""

    # 🎙 음성 클로닝 토픽 → 텍스트를 음성으로 합성 (YouTube URL 불필요)
    if tid and tid == TG_TOPIC_TTS:
        if not text.strip():
            await _send_telegram(chat_id, "합성할 텍스트를 보내주세요.", thread_id)
            return {"ok": True, "action": "tts_empty"}
        await _send_telegram(chat_id, "🎙 음성 합성 중...", thread_id)
        background_tasks.add_task(_run_tts_telegram, text, chat_id, thread_id)
        return {"ok": True, "action": "tts_started"}

    # 나머지(요약/블로그/mp3/일반)는 YouTube URL 필요
    match = _YT_URL_PATTERN.search(text)
    if not match:
        await _send_telegram(
            chat_id, "YouTube URL을 보내주세요.\n예: https://www.youtube.com/watch?v=XXXX", thread_id)
        return {"ok": True, "action": "invalid_url"}

    url = match.group(0)
    if not url.startswith("http"):
        url = "https://" + url

    if not _PIPELINE_AVAILABLE:
        await _send_telegram(chat_id, "⚠️ 파이프라인 모듈을 찾을 수 없습니다.", thread_id)
        return {"ok": False, "detail": "pipeline unavailable"}

    # 📰 블로그 토픽 또는 "블로그"/"blog" 키워드 → WordPress 초안 발행
    if (tid and tid == TG_TOPIC_BLOG) or "블로그" in text or "blog" in text.lower():
        await _send_telegram(chat_id, f"⏳ 블로그 글 생성 시작\n{url}\n\n완료되면 편집 링크를 보내드릴게요!", thread_id)
        background_tasks.add_task(_run_blog_pipeline, url, chat_id, None, thread_id)
        return {"ok": True, "action": "blog_started", "url": url}

    # "mp3" 키워드 → MP3 다운로드 링크만 (일반 토픽 폴백)
    if "mp3" in text.lower():
        await _send_telegram(chat_id, f"⏳ MP3 추출 시작\n{url}\n\n완료되면 링크를 보내드릴게요!", thread_id)
        background_tasks.add_task(_run_mp3_pipeline, url, chat_id, thread_id)
        return {"ok": True, "action": "mp3_started", "url": url}

    # 📺 영상 요약 토픽 또는 일반(기본) → STT + 요약 (동시 요청은 _pipeline_lock으로 순차 처리)
    if _pipeline_lock.locked():
        await _send_telegram(
            chat_id,
            f"⏳ 대기열에 추가됐어요\n{url}\n\n현재 다른 영상을 처리 중입니다. 끝나면 순서대로 처리할게요!",
            thread_id)
        action = "pipeline_queued"
    else:
        await _send_telegram(
            chat_id, f"⏳ 처리 시작\n{url}\n\n모델: {STT_MODEL}\n완료되면 알려드릴게요!", thread_id)
        action = "pipeline_started"

    background_tasks.add_task(_run_video_pipeline, url, STT_MODEL, None, chat_id, thread_id)
    return {"ok": True, "action": action, "url": url}


@app.post("/publish/youtube", summary="YouTube 영상 → 블로그 글 생성 → WordPress 초안 발행")
async def publish_youtube(
    background_tasks: BackgroundTasks,
    url:     str           = Query(...,             description="YouTube 영상 URL"),
    status:  Optional[str] = Query(default=None,    description="draft | publish (기본: WP_DEFAULT_STATUS)"),
    chat_id: Optional[str] = Query(default=None,    description="Telegram Chat ID (미지정 시 환경변수)"),
):
    """YouTube URL → STT → 블로그 글(LLM) → WordPress 초안 생성. 완료 시 Telegram 알림."""
    if not _WP_AVAILABLE:
        raise HTTPException(status_code=503, detail="wp_publish 모듈을 사용할 수 없습니다.")
    if not (WP_USER and WP_APP_PASSWORD):
        raise HTTPException(status_code=503, detail="WordPress 인증 미설정(WP_USER/WP_APP_PASSWORD).")
    cid = chat_id or TELEGRAM_CHAT_ID
    background_tasks.add_task(_run_blog_pipeline, url, cid, status)
    return {"status": "started", "url": url, "wp_status": status or WP_DEFAULT_STATUS}


# ──────────────────────────────────────────────
# 일일 리포트 (HTML 렌더링 + Telegram 알림)
# ──────────────────────────────────────────────

try:
    import markdown as _md_pkg
    _MARKDOWN_AVAILABLE = True
except ImportError:
    _md_pkg = None  # type: ignore
    _MARKDOWN_AVAILABLE = False


def _render_md_html(title: str, md_content: str) -> str:
    """마크다운 문자열을 모바일 친화적 HTML로 변환."""
    if _MARKDOWN_AVAILABLE and md_content:
        body_html = _md_pkg.markdown(md_content, extensions=["tables", "fenced_code"])
    elif md_content:
        # markdown 패키지 없으면 <pre>로 폴백
        body_html = f"<pre>{md_content}</pre>"
    else:
        body_html = "<p style='color:#888'>수집된 영상이 없습니다.</p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{ --accent: #4f8ef7; --bg: #0f1117; --card: #1a1d27; --text: #e2e8f0; --sub: #94a3b8; --border: #2d3348; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: var(--bg); color: var(--text); padding: 16px; font-size: 15px; line-height: 1.65; }}
  h1 {{ font-size: 1.25rem; color: var(--accent); margin-bottom: 4px; }}
  h2 {{ font-size: 1.1rem; color: var(--text); margin: 24px 0 8px; border-left: 3px solid var(--accent); padding-left: 10px; }}
  h3 {{ font-size: 0.97rem; color: var(--text); margin: 18px 0 6px; padding: 10px 12px;
        background: var(--card); border-radius: 8px; border: 1px solid var(--border); }}
  p {{ margin: 6px 0; color: var(--sub); font-size: 0.9rem; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  blockquote {{ margin: 8px 0 12px 0; padding: 10px 14px; background: #1e2333;
                border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0;
                font-size: 0.88rem; color: var(--sub); white-space: pre-wrap; }}
  ul {{ padding-left: 18px; margin: 4px 0 8px; }}
  li {{ margin: 3px 0; font-size: 0.9rem; }}
  strong {{ color: var(--text); }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 20px 0; }}
  .header {{ margin-bottom: 20px; }}
  .tag {{ font-size: 0.78rem; color: var(--sub); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; margin: 8px 0; }}
  th {{ background: var(--card); padding: 6px 10px; text-align: left; border: 1px solid var(--border); }}
  td {{ padding: 5px 10px; border: 1px solid var(--border); }}
  img {{ max-width: 100%; height: auto; display: block; border-radius: 6px; margin: 8px 0; }}
</style>
</head>
<body>
<div class="header">
  <h1>📺 {title}</h1>
  <span class="tag">잇츠매거진 콘텐츠 리서치</span>
</div>
{body_html}
</body>
</html>"""


@app.get("/summary/view", response_class=HTMLResponse, summary="영상 요약 .md를 HTML로 보기")
async def summary_view(
    file: str = Query(..., description="summaries/ 내 .md 파일명"),
):
    """단일 영상 요약(.md)을 모바일 친화적 HTML로 렌더링한다.
    Telegram 링크(`/summary/view?file=...`)로 바로 열어 읽을 수 있다.
    """
    safe_name = Path(file).name                       # 경로 탈출 방지
    path = Path(__file__).parent / "summaries" / safe_name
    if path.suffix.lower() != ".md" or not path.exists():
        raise HTTPException(status_code=404, detail="요약 파일을 찾을 수 없습니다.")

    md = path.read_text(encoding="utf-8")
    title = path.stem
    for line in md.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return HTMLResponse(content=_render_md_html(title, md))


@app.get("/telegram/webhook-info", summary="현재 등록된 Telegram 웹훅 정보 조회")
async def telegram_webhook_info():
    """Telegram Bot API에서 현재 웹훅 상태를 조회한다."""
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN 미설정")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo",
            timeout=10,
        )
    return resp.json()


@app.get("/telegram/register-webhook", summary="Telegram 웹훅 수동 등록")
async def telegram_register_webhook():
    """FASTAPI_PUBLIC_URL 기준으로 웹훅을 즉시 등록한다. 서버 재시작 없이 수동 호출 가능."""
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN 미설정")
    webhook_url = f"{FASTAPI_PUBLIC_URL.rstrip('/')}/pipeline/telegram"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url},
            timeout=10,
        )
    data = resp.json()
    if data.get("ok"):
        logger.info(f"[Telegram] 웹훅 수동 등록 완료: {webhook_url}")
    else:
        logger.warning(f"[Telegram] 웹훅 수동 등록 실패: {data.get('description')}")
    return {**data, "webhook_url": webhook_url}


# ──────────────────────────────────────────────
# research/config — 설정 파일 파싱 헬퍼
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "fastapi_linux:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info",
    )
