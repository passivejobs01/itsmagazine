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
import subprocess
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
from datetime import datetime, timedelta, timezone

# faster-whisper (STT) — 설치 안 된 경우에도 서버 기동은 정상.
# 실제 STT는 video_pipeline.transcribe에서 수행하므로 여기서는 가용 여부만 확인한다.
import importlib.util
_WHISPER_AVAILABLE = importlib.util.find_spec("faster_whisper") is not None

import httpx
import yt_dlp
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
        check_ollama,
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
MY_CHANNEL_URL = os.getenv("MY_CHANNEL_URL", "")

PORT = int(os.getenv("PORT", "8003"))

# YouTube Data API v3
YOUTUBE_API_KEY  = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

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

# 로컬 파일 저장 경로 (FastAPI가 직접 파일을 쓸 때 사용)
RESEARCH_BASE_DIR = os.getenv("RESEARCH_BASE_DIR", "")

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

def _format_duration(seconds) -> str:
    """초 → MM:SS / HH:MM:SS"""
    if not seconds:
        return None
    seconds = int(seconds)  # yt-dlp가 float으로 반환하는 경우 대응
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _format_date(date_str: Optional[str]) -> Optional[str]:
    """YYYYMMDD → YYYY-MM-DD"""
    if not date_str or len(date_str) != 8:
        return None
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def _parse_iso_duration(duration_str: Optional[str]) -> Optional[str]:
    """ISO 8601 duration (PT4M33S) → MM:SS / HH:MM:SS"""
    if not duration_str:
        return None
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return _format_duration(h * 3600 + mins * 60 + s)


def _iso_duration_seconds(duration_str: Optional[str]) -> int:
    """ISO 8601 duration (PT4M33S) → 총 초. 파싱 실패 시 0 반환."""
    if not duration_str:
        return 0
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mins * 60 + s


SHORTS_MAX_SECONDS = 120   # 120초 이하는 쇼츠로 간주
LONG_MAX_SECONDS   = 3600  # 3600초(1시간) 초과는 장시간 영상으로 제외


# ──────────────────────────────────────────────
# yt-dlp 수집 로직 (동기, Executor에서 실행)
# ──────────────────────────────────────────────

def _is_valid_video_id(video_id: str) -> bool:
    """YouTube 영상 ID인지 확인 (채널/플레이리스트 ID 제외)"""
    if not video_id or len(video_id) < 8:
        return False
    # 채널 ID(UC...), 플레이리스트(PL...), 기타 비영상 ID 제외
    if video_id.startswith(("UC", "PL", "LL", "FL", "WL", "RD")):
        return False
    return True


def _fetch_channel_flat(channel_url: str, limit: int) -> tuple[list[dict], str]:
    """
    extract_flat으로 채널의 최신 영상 ID/제목만 빠르게 수집한다.
    """
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": limit,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if not info:
        raise ValueError("채널 정보를 가져오지 못했습니다.")

    channel_name = (
        info.get("channel") or info.get("uploader") or info.get("title") or "Unknown"
    )

    def flatten(entries_list):
        result = []
        for e in (entries_list or []):
            if not e:
                continue
            if e.get("_type") == "playlist":
                result.extend(flatten(e.get("entries") or []))
            else:
                result.append(e)
        return result

    entries = flatten(info.get("entries") or [])
    videos = []
    for entry in entries:
        video_id = entry.get("id") or ""
        if not _is_valid_video_id(video_id):
            continue
        url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={video_id}"
        videos.append({
            "video_id": video_id,
            "title": entry.get("title") or "",
            "url": url,
            "upload_date": None,
            "duration": None,
            "view_count": None,
            "description": "",
        })

    return videos, channel_name


def _fetch_video_full(video_id: str) -> dict:
    """
    단일 영상의 full 메타데이터를 수집한다 (날짜·조회수·설명 포함).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        return {}

    upload_date_raw = info.get("upload_date")
    upload_date = (
        f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:]}"
        if upload_date_raw and len(upload_date_raw) == 8 else None
    )
    return {
        "upload_date": upload_date,
        "duration": _format_duration(info.get("duration")),
        "view_count": info.get("view_count"),
        "description": (info.get("description") or "")[:400],
    }


def _fetch_channel_recent_full(channel_url: str, limit: int, days: int, exclude_ids: set) -> tuple[list[dict], str]:
    """
    2단계 수집:
    1단계) extract_flat으로 최신 N개 영상 ID 빠르게 수집
    2단계) exclude_ids에 없는 신규 영상만 full 메타데이터 수집 (날짜·조회수·설명)
    날짜 필터는 Python에서 처리.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    logger.info(f"[연구수집] 1단계: flat 수집 시작 — {channel_url}")
    videos, channel_name = _fetch_channel_flat(channel_url, limit)
    logger.info(f"[연구수집] 1단계 완료: {channel_name} — {len(videos)}개 ID 확보")

    enriched = []
    for v in videos:
        # 이미 수집된 영상은 full 수집 없이 스킵
        if v["video_id"] in exclude_ids:
            logger.info(f"[연구수집] 이미 수집됨, 스킵: {v['video_id']}")
            continue

        logger.info(f"[연구수집] 2단계: full 수집 — {v['video_id']}")
        full = _fetch_video_full(v["video_id"])
        v.update(full)

        # 날짜 필터 (upload_date가 있으면 적용)
        if v.get("upload_date"):
            try:
                upload_dt = datetime.strptime(v["upload_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if upload_dt < cutoff:
                    logger.info(f"[연구수집] 날짜 필터 스킵: {v['video_id']} ({v['upload_date']})")
                    continue
            except ValueError:
                pass

        enriched.append(v)

    logger.info(f"[연구수집] 완료: {channel_name} — {len(enriched)}개 신규 (최근 {days}일)")
    return enriched, channel_name


# ──────────────────────────────────────────────
# 자막 추출 로직 (yt-dlp 자막 다운로드)
# ──────────────────────────────────────────────


def _fetch_subtitles(video_id: str) -> str:
    """
    youtube-transcript-api 1.2.4로 YouTube 자막을 가져온다.
    한국어 우선, 없으면 영어. 수동 자막 우선, 없으면 자동생성 자막.
    반환: 순수 텍스트 (없으면 빈 문자열)
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.warning("[자막] youtube-transcript-api 미설치. pip install youtube-transcript-api")
        return ""

    try:
        ytt = YouTubeTranscriptApi()
        fetched = ytt.fetch(video_id, languages=["ko", "en"])
        text = " ".join(s.text.strip() for s in fetched if s.text.strip())
        logger.info(f"[자막] 추출 완료: {video_id} ({len(text)}자)")
        return text

    except Exception as e:
        logger.warning(f"[자막] 추출 실패 {video_id}: {type(e).__name__}: {e}")
        return ""


# ──────────────────────────────────────────────
# Whisper STT 로직 (동기, Executor에서 실행)
# ──────────────────────────────────────────────

_DEFAULT_TRANSCRIPT_DIR = Path(__file__).parent / "transcripts"


def _run_transcribe(
    url: str,
    model_size: str,
    language: Optional[str],
    output_dir: Path,
    keep_audio: bool,
) -> dict:
    """동기 변환 워커 — Executor에서 실행.
    오디오 다운로드·STT는 video_pipeline의 공용 함수를 재사용한다."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 오디오 다운로드 (output_dir에 임시 저장 — keep_audio=False면 마지막에 삭제)
    logger.info(f"[Transcribe] 오디오 다운로드 시작: {url}")
    audio_path, metadata = prepare_audio(url, output_dir)
    title = metadata.get("title", "video")
    logger.info(f"[Transcribe] 다운로드 완료: {audio_path.name}")

    # 2. STT (모델 캐시는 video_pipeline에서 관리, device는 STT_DEVICE 환경변수)
    transcript, detected_lang = transcribe(audio_path, model_size, STT_DEVICE, language, 4)
    logger.info(f"[Transcribe] 완료: 언어={detected_lang}")

    # 3. 파일 저장
    safe_title = sanitize_filename(title)
    out_path = output_dir / f"{safe_title}.txt"
    header_lines = [f"# {title}"]
    if metadata.get("url"):
        header_lines.append(f"# 영상주소: {metadata['url']}")
    if metadata.get("channel"):
        ch = f"# 채널명: {metadata['channel']}"
        if metadata.get("channel_url"):
            ch += f" ({metadata['channel_url']})"
        header_lines.append(ch)
    header_lines.extend([f"# language: {detected_lang}", "", ""])
    out_path.write_text("\n".join(header_lines) + transcript, encoding="utf-8")
    logger.info(f"[Transcribe] 저장: {out_path}")

    # 4. 임시 오디오 정리
    if not keep_audio:
        try:
            audio_path.unlink()
        except OSError:
            pass

    return {
        "title": title,
        "url": metadata.get("url", url),
        "channel": metadata.get("channel", ""),
        "channel_url": metadata.get("channel_url", ""),
        "language": detected_lang,
        "transcript": transcript,
        "saved_path": str(out_path),
    }


# ──────────────────────────────────────────────
# Ollama 요약 로직 (비동기)
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

async def _resolve_channel_id(channel_url: str, client: httpx.AsyncClient) -> tuple[str, str]:
    """
    채널 URL(@handle)에서 채널 ID와 채널명을 반환한다.
    YouTube Data API channels.list 사용.
    """
    handle_m = re.search(r'@([\w\-]+)', channel_url)
    if handle_m:
        handle = handle_m.group(1)
        resp = await client.get(
            f"{YOUTUBE_API_BASE}/channels",
            params={"forHandle": handle, "part": "id,snippet", "key": YOUTUBE_API_KEY},
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            raise ValueError(f"채널을 찾을 수 없습니다: @{handle}")
        return items[0]["id"], items[0]["snippet"]["title"]

    id_m = re.search(r'/channel/(UC[\w\-]+)', channel_url)
    if id_m:
        channel_id = id_m.group(1)
        resp = await client.get(
            f"{YOUTUBE_API_BASE}/channels",
            params={"id": channel_id, "part": "snippet", "key": YOUTUBE_API_KEY},
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        channel_name = items[0]["snippet"]["title"] if items else "Unknown"
        return channel_id, channel_name

    raise ValueError(f"채널 ID를 파싱할 수 없습니다: {channel_url}")


async def _fetch_channel_recent_api(
    channel_url: str,
    max_results: int,
    exclude_ids: set,
    client: httpx.AsyncClient,
    include_subtitles: bool = False,
    include_summary: bool = False,
) -> tuple[list[dict], str]:
    """
    YouTube Data API v3로 채널 최신 영상 수집.
    1) channels.list → 채널 ID 확인
    2) playlistItems.list (업로드 플레이리스트 UUxxxx) → 최신 영상 목록
    3) videos.list (statistics + contentDetails) → 조회수 + 길이 batch 조회
    날짜 필터 없이 최신순으로 max_results개 수집.
    """
    channel_id, channel_name = await _resolve_channel_id(channel_url, client)
    upload_playlist_id = "UU" + channel_id[2:]
    logger.info(f"[YT API] {channel_name} ({channel_id}), 플레이리스트: {upload_playlist_id}")

    fetch_count = min(max_results, 50)
    resp = await client.get(
        f"{YOUTUBE_API_BASE}/playlistItems",
        params={
            "playlistId": upload_playlist_id,
            "maxResults": fetch_count,
            "part": "snippet",
            "key": YOUTUBE_API_KEY,
        },
        timeout=15.0,
    )
    resp.raise_for_status()

    playlist_items = resp.json().get("items", [])
    video_ids = []
    snippets: dict[str, dict] = {}

    for item in playlist_items:
        snippet = item.get("snippet", {})
        video_id = snippet.get("resourceId", {}).get("videoId", "")

        if not video_id or video_id in exclude_ids:
            continue

        video_ids.append(video_id)
        snippets[video_id] = snippet
        if len(video_ids) >= max_results:
            break

    if not video_ids:
        logger.info(f"[YT API] {channel_name} — 수집 대상 없음")
        return [], channel_name

    # 조회수 + 길이 batch 조회
    resp2 = await client.get(
        f"{YOUTUBE_API_BASE}/videos",
        params={
            "id": ",".join(video_ids),
            "part": "statistics,contentDetails",
            "key": YOUTUBE_API_KEY,
        },
        timeout=15.0,
    )
    resp2.raise_for_status()
    video_details = {v["id"]: v for v in resp2.json().get("items", [])}

    videos = []
    for vid in video_ids:
        snippet = snippets[vid]
        details = video_details.get(vid, {})
        stats = details.get("statistics", {})
        content = details.get("contentDetails", {})

        published_at = snippet.get("publishedAt", "")
        upload_date = published_at[:10] if published_at else None  # YYYY-MM-DD

        view_count = int(stats["viewCount"]) if stats.get("viewCount") else None
        iso_dur = content.get("duration", "")
        duration = _parse_iso_duration(iso_dur)

        # 길이 필터: 쇼츠(너무 짧음) 또는 장시간 영상(너무 김) 제외
        dur_sec = _iso_duration_seconds(iso_dur)
        if dur_sec <= SHORTS_MAX_SECONDS:
            logger.debug(f"[YT API] 쇼츠 제외: {vid} ({iso_dur})")
            continue
        if dur_sec > LONG_MAX_SECONDS:
            logger.debug(f"[YT API] 장시간 영상 제외: {vid} ({iso_dur})")
            continue

        # 자막 추출 (동기 함수이므로 executor에서 실행)
        subtitles = ""
        if include_subtitles:
            loop = asyncio.get_event_loop()
            subtitles = await loop.run_in_executor(_executor, _fetch_subtitles, vid)
            await asyncio.sleep(1.0)  # 연속 호출 방지

        # Ollama 요약 (자막이 있을 때만)
        summary = ""
        if include_summary and subtitles:
            summary = await _summarize_with_ollama(subtitles, client)

        videos.append({
            "video_id": vid,
            "title": snippet.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "upload_date": upload_date,
            "view_count": view_count,
            "duration": duration,
            "description": (snippet.get("description", "") or "")[:400],
            "subtitles": subtitles,
            "summary": summary,
        })

    logger.info(f"[YT API] {channel_name} — {len(videos)}개 신규 (최신순, 쇼츠 제외)")
    return videos, channel_name


async def _search_keyword_videos_api(
    keyword: str,
    days: int,
    max_results: int,
    exclude_ids: set,
    client: httpx.AsyncClient,
    include_subtitles: bool = False,
    include_summary: bool = False,
) -> list[dict]:
    """
    YouTube Data API search.list로 키워드 검색.
    조회수 높은 순, 최근 N일 이내, exclude_ids 제외.
    쇼츠 필터링 후 max_results개를 채우기 위해 여유있게 요청 (최대 50).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    published_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 쇼츠 필터링 + exclude_ids 손실을 고려해 여유있게 요청 (최대 50개)
    fetch_count = min(max_results * 3 + len(exclude_ids), 50)

    resp = await client.get(
        f"{YOUTUBE_API_BASE}/search",
        params={
            "q": keyword,
            "type": "video",
            "part": "snippet",
            "maxResults": fetch_count,
            "order": "viewCount",
            "publishedAfter": published_after,
            "key": YOUTUBE_API_KEY,
        },
        timeout=15.0,
    )
    resp.raise_for_status()

    items = resp.json().get("items", [])
    video_ids = []
    snippets: dict[str, dict] = {}

    for item in items:
        vid = item.get("id", {}).get("videoId", "")
        if not vid or vid in exclude_ids:
            continue
        video_ids.append(vid)
        snippets[vid] = item.get("snippet", {})

    if not video_ids:
        logger.info(f"[keyword] '{keyword}' — 신규 영상 없음")
        return []

    # 조회수 + 길이 batch 조회
    resp2 = await client.get(
        f"{YOUTUBE_API_BASE}/videos",
        params={"id": ",".join(video_ids), "part": "statistics,contentDetails", "key": YOUTUBE_API_KEY},
        timeout=15.0,
    )
    resp2.raise_for_status()
    video_details = {v["id"]: v for v in resp2.json().get("items", [])}

    videos = []
    for vid in video_ids:
        snippet = snippets[vid]
        details = video_details.get(vid, {})
        stats = details.get("statistics", {})
        content = details.get("contentDetails", {})

        published_at = snippet.get("publishedAt", "")
        upload_date = published_at[:10] if published_at else None
        view_count = int(stats["viewCount"]) if stats.get("viewCount") else None
        iso_dur = content.get("duration", "")
        duration = _parse_iso_duration(iso_dur)

        # 길이 필터: 쇼츠(너무 짧음) 또는 장시간 영상(너무 김) 제외
        dur_sec = _iso_duration_seconds(iso_dur)
        if dur_sec <= SHORTS_MAX_SECONDS:
            logger.debug(f"[keyword] 쇼츠 제외: {vid} ({iso_dur})")
            continue
        if dur_sec > LONG_MAX_SECONDS:
            logger.debug(f"[keyword] 장시간 영상 제외: {vid} ({iso_dur})")
            continue

        subtitles = ""
        if include_subtitles:
            loop = asyncio.get_event_loop()
            subtitles = await loop.run_in_executor(_executor, _fetch_subtitles, vid)
            await asyncio.sleep(1.0)

        summary = ""
        if include_summary and subtitles:
            summary = await _summarize_with_ollama(subtitles, client)

        videos.append({
            "video_id": vid,
            "title": snippet.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel_name": snippet.get("channelTitle", ""),
            "channel_id": snippet.get("channelId", ""),
            "upload_date": upload_date,
            "view_count": view_count,
            "duration": duration,
            "description": (snippet.get("description", "") or "")[:400],
            "subtitles": subtitles,
            "summary": summary,
            "matched_keyword": keyword,
        })

    # 쇼츠 필터링 후 max_results개로 제한
    videos = videos[:max_results]
    logger.info(f"[keyword] '{keyword}' — {len(videos)}개 수집 (쇼츠 제외, {fetch_count}개 중)")
    return videos


def _fetch_channel_videos(channel_url: str, limit: Optional[int]) -> tuple[list[dict], str]:
    """
    yt-dlp로 채널 영상 메타데이터를 수집한다.

    Returns:
        (videos, channel_name)
    """
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",  # 중첩 플레이리스트까지 평탄화
        "skip_download": True,
    }
    if limit:
        ydl_opts["playlistend"] = limit

    logger.info(f"[yt-dlp] 수집 시작: {channel_url} (limit={limit})")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if not info:
        raise ValueError("채널 정보를 가져오지 못했습니다.")

    channel_name = (
        info.get("channel")
        or info.get("uploader")
        or info.get("title")
        or "Unknown"
    )

    # 채널 탭 구조 재귀 평탄화 (Videos 탭 + Shorts 탭 등 중첩 처리)
    def flatten(entries_list):
        result = []
        for e in (entries_list or []):
            if not e:
                continue
            if e.get("_type") == "playlist":
                result.extend(flatten(e.get("entries") or []))
            else:
                result.append(e)
        return result

    flat = flatten(info.get("entries") or [])

    videos = []
    for i, entry in enumerate(flat, 1):
        video_id = entry.get("id") or ""
        url = entry.get("url") or entry.get("webpage_url") or ""
        if video_id and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={video_id}"

        videos.append({
            "no":          i,
            "video_id":    video_id,
            "title":       entry.get("title") or "",
            "url":         url,
            "upload_date": _format_date(entry.get("upload_date")),
            "duration":    _format_duration(entry.get("duration")),
            "duration_sec": int(entry["duration"]) if entry.get("duration") is not None else None,
            "view_count":  entry.get("view_count"),
            "description": (entry.get("description") or "")[:300],
        })

    logger.info(f"[yt-dlp] 완료: {channel_name} — {len(videos)}개")
    return videos, channel_name


# ──────────────────────────────────────────────
# Pydantic 응답 모델
# ──────────────────────────────────────────────

class VideoItem(BaseModel):
    no:           int
    video_id:     str
    title:        str
    url:          str
    upload_date:  Optional[str]
    duration:     Optional[str]
    duration_sec: Optional[int]
    view_count:   Optional[int]
    description:  str


class ChannelVideosResponse(BaseModel):
    channel_name: str
    channel_url:  str
    total:        int
    videos:       list[VideoItem]


class RecentVideoItem(BaseModel):
    video_id:     str
    title:        str
    url:          str
    channel_name: str
    channel_url:  str
    upload_date:  Optional[str]
    view_count:   Optional[int]
    duration:     Optional[str]
    description:  str
    subtitles:    str = ""   # 자막 텍스트 (미요청 또는 없으면 빈 문자열)
    summary:      str = ""   # 한국어 요약 (미요청 또는 자막 없으면 빈 문자열)


class RecentVideosResponse(BaseModel):
    collected_at: str
    total:        int
    videos:       list[RecentVideoItem]


class KeywordVideoItem(BaseModel):
    video_id:         str
    title:            str
    url:              str
    channel_name:     str
    channel_id:       str
    upload_date:      Optional[str]
    view_count:       Optional[int]
    duration:         Optional[str]
    description:      str
    subtitles:        str = ""
    summary:          str = ""
    matched_keyword:  str


class KeywordVideosResponse(BaseModel):
    collected_at: str
    days:         int
    total:        int
    videos:       list[KeywordVideoItem]


class TranscribeRequest(BaseModel):
    url:        str
    model:      str = "large-v3"
    language:   Optional[str] = None
    keep_audio: bool = False
    output_dir: Optional[str] = None  # 저장 경로 (미지정 시 기본 경로 사용)


class TranscribeResponse(BaseModel):
    title:       str
    url:         str
    channel:     str
    channel_url: str
    language:    str
    transcript:  str
    saved_path:  str


# ──────────────────────────────────────────────
# API 엔드포인트
# ──────────────────────────────────────────────

@app.get("/", summary="헬스 체크")
async def health_check():
    return {"status": "ok", "server": "Content Tools API", "host": FASTAPI_PUBLIC_URL}


@app.get(
    "/youtube/channel-videos",
    response_model=ChannelVideosResponse,
    summary="YouTube 채널 영상 목록 조회",
    description=(
        "지정한 YouTube 채널의 영상 목록과 메타데이터(제목·날짜·조회수·길이·설명)를 반환합니다. "
        "yt-dlp를 사용하므로 별도 API 키가 필요 없습니다."
    ),
)
async def get_channel_videos(
    channel_url: str = Query(
        ...,
        description="YouTube 채널 URL (예: https://www.youtube.com/@channel-id)",
    ),
    limit: Optional[int] = Query(
        default=None,
        description="수집할 최대 영상 수. 생략 시 전체 수집.",
        ge=1,
        le=500,
    ),
):
    try:
        loop = asyncio.get_event_loop()
        videos, channel_name = await loop.run_in_executor(
            _executor, _fetch_channel_videos, channel_url, limit
        )
        return ChannelVideosResponse(
            channel_name=channel_name,
            channel_url=channel_url,
            total=len(videos),
            videos=videos,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[ERROR] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/youtube/my-channel",
    response_model=ChannelVideosResponse,
    summary="내 채널 영상 목록 조회",
    description=(
        f"사전 설정된 내 채널({MY_CHANNEL_URL})의 영상 목록을 반환합니다. "
        "channel_url 없이 호출할 수 있는 편의 엔드포인트입니다."
    ),
)
async def get_my_channel_videos(
    limit: Optional[int] = Query(
        default=None,
        description="수집할 최대 영상 수. 생략 시 전체 수집.",
        ge=1,
        le=500,
    ),
):
    try:
        loop = asyncio.get_event_loop()
        videos, channel_name = await loop.run_in_executor(
            _executor, _fetch_channel_videos, MY_CHANNEL_URL, limit
        )
        return ChannelVideosResponse(
            channel_name=channel_name,
            channel_url=MY_CHANNEL_URL,
            total=len(videos),
            videos=videos,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"[ERROR] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/research/recent-videos",
    response_model=RecentVideosResponse,
    summary="모니터링 채널 최근 영상 수집 (YouTube Data API v3)",
    description=(
        "지정한 채널 URL들에서 최근 N일 이내에 업로드된 영상을 수집해 반환합니다. "
        "YouTube Data API v3를 사용합니다. 환경변수 YOUTUBE_API_KEY가 필요합니다."
    ),
)
async def get_recent_videos(
    channel_urls: str = Query(
        ...,
        description="쉼표로 구분된 채널 URL 목록 (예: https://www.youtube.com/@CraftComputing,https://www.youtube.com/@TechHut)",
    ),
    limit_per_channel: int = Query(
        default=10,
        description="채널당 최대 수집 영상 수 (기본 10개)",
        ge=1,
        le=50,
    ),
    exclude_ids: str = Query(
        default="",
        description="이미 수집된 영상 ID 목록 (쉼표 구분). 중복 수집을 건너뜁니다.",
    ),
    include_subtitles: bool = Query(
        default=False,
        description="자막 추출 여부. True면 영상당 자막 텍스트를 함께 반환합니다 (속도 느려짐).",
    ),
    include_summary: bool = Query(
        default=False,
        description="Ollama 요약 여부. True면 자막을 한국어로 요약합니다 (include_subtitles=True 필요).",
    ),
):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY 환경변수가 설정되지 않았습니다.")

    urls = [u.strip() for u in channel_urls.split(",") if u.strip()]
    if not urls:
        logger.info("[research] 활성 채널 없음 — 스킵")
        return RecentVideosResponse(
            collected_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            total=0, videos=[],
        )

    exclude_set = {vid.strip() for vid in exclude_ids.split(",") if vid.strip()}
    logger.info(f"[research] 채널 {len(urls)}개, exclude_ids: {len(exclude_set)}개")

    all_videos: list[RecentVideoItem] = []

    async with httpx.AsyncClient() as client:
        async def fetch_one(url: str):
            try:
                videos, channel_name = await _fetch_channel_recent_api(
                    url, limit_per_channel, exclude_set, client,
                    include_subtitles, include_summary,
                )
                return [
                    RecentVideoItem(
                        video_id=v["video_id"],
                        title=v["title"],
                        url=v["url"],
                        channel_name=channel_name,
                        channel_url=url,
                        upload_date=v["upload_date"],
                        view_count=v.get("view_count"),
                        duration=v.get("duration"),
                        description=v.get("description", ""),
                        subtitles=v.get("subtitles", ""),
                        summary=v.get("summary", ""),
                    )
                    for v in videos
                ]
            except Exception as e:
                logger.error(f"[research] 채널 수집 실패 {url}: {e}")
                return []

        results = await asyncio.gather(*[fetch_one(u) for u in urls])

    for r in results:
        all_videos.extend(r)

    all_videos.sort(key=lambda v: v.upload_date or "", reverse=True)

    return RecentVideosResponse(
        collected_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        total=len(all_videos),
        videos=all_videos,
    )


@app.get(
    "/research/keyword-videos",
    response_model=KeywordVideosResponse,
    summary="키워드 기반 YouTube 영상 검색",
    description=(
        "지정한 키워드로 YouTube 영상을 검색합니다. "
        "조회수 높은 순, 최근 N일 이내 업로드 필터. YouTube Data API v3 사용."
    ),
)
async def get_keyword_videos(
    keywords: str = Query(
        ...,
        description="쉼표로 구분된 키워드 목록 (예: home server 2025,홈서버,local AI)",
    ),
    days: int = Query(default=30, ge=1, le=90, description="최근 N일 이내 업로드 (기본 30일)"),
    max_results: int = Query(default=5, ge=1, le=20, description="키워드당 최대 수집 수 (기본 5개)"),
    exclude_ids: str = Query(default="", description="제외할 영상 ID 목록 (쉼표 구분)"),
    include_subtitles: bool = Query(default=False, description="자막 추출 여부"),
    include_summary: bool = Query(default=False, description="Ollama 한국어 요약 여부"),
):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY 환경변수가 설정되지 않았습니다.")

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not kw_list:
        logger.info("[keyword] 활성 키워드 없음 — 스킵")
        return KeywordVideosResponse(
            collected_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            days=days, total=0, videos=[],
        )

    exclude_set = {vid.strip() for vid in exclude_ids.split(",") if vid.strip()}
    logger.info(f"[keyword] 키워드 {len(kw_list)}개, exclude: {len(exclude_set)}개")

    all_videos: list[KeywordVideoItem] = []

    async with httpx.AsyncClient() as client:
        for keyword in kw_list:
            try:
                videos = await _search_keyword_videos_api(
                    keyword, days, max_results, exclude_set, client,
                    include_subtitles, include_summary,
                )
                for v in videos:
                    all_videos.append(KeywordVideoItem(**v))
                    exclude_set.add(v["video_id"])  # 같은 실행 내 키워드 간 중복 방지
            except Exception as e:
                logger.error(f"[keyword] '{keyword}' 수집 실패: {type(e).__name__}: {e}")

    all_videos.sort(key=lambda v: v.view_count or 0, reverse=True)

    return KeywordVideosResponse(
        collected_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        days=days,
        total=len(all_videos),
        videos=all_videos,
    )


class KeywordCollectRequest(BaseModel):
    keywords:          str
    days:              int = 30
    max_results:       int = 5
    exclude_ids:       str = ""
    include_subtitles: bool = False
    include_summary:   bool = False


def _write_keyword_md(videos: list[dict], base_dir: str):
    """키워드 수집 결과를 로컬 파일에 직접 저장."""
    save_dir = Path(base_dir) / "키워드"
    save_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    file_path = save_dir / f"{today}.md"

    # 기존 수집 videoId 스캔 (채널 + 키워드 폴더)
    collected_ids: set[str] = set()
    import re as _re
    for sub in ["채널", "키워드"]:
        sub_dir = Path(base_dir) / sub
        if not sub_dir.exists():
            continue
        for f in sub_dir.glob("*.md"):
            content = f.read_text(encoding="utf-8", errors="ignore")
            for m in _re.finditer(r"watch\?v=([\w-]{11})", content):
                collected_ids.add(m.group(1))

    # 키워드별 그룹핑 (신규만)
    by_keyword: dict[str, list[dict]] = {}
    for v in videos:
        vid = v.get("video_id", "")
        if not vid or vid in collected_ids:
            continue
        kw = v.get("matched_keyword", "기타")
        by_keyword.setdefault(kw, []).append(v)

    total_new = sum(len(vl) for vl in by_keyword.values())
    if total_new == 0:
        logger.info("[keyword-async] 신규 영상 없음, 파일 미생성")
        return {"saved": 0, "skipped": len(videos), "videoUrls": "", "today": today}

    all_keywords = ", ".join(dict.fromkeys(v.get("matched_keyword", "") for v in videos))
    header = f"# 키워드 검색 — {today}\n\n키워드: {all_keywords} | 수집: {total_new}개\n"
    file_path.write_text(header, encoding="utf-8")

    for keyword, kw_videos in by_keyword.items():
        with file_path.open("a", encoding="utf-8") as f:
            f.write(f"\n---\n\n## 🔍 {keyword}\n")
            for v in kw_videos:
                views = f"{v['view_count']:,}" if v.get("view_count") else "-"
                vid = v.get("video_id", "")
                thumb = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else ""
                lines = [
                    "",
                    f"### [{v['channel_name']}] {v['title']}",
                    "",
                ]
                if thumb:
                    lines.append(f"![썸네일]({thumb})")
                    lines.append("")
                lines += [
                    f"**URL**: {v['url']}  ",
                    f"**업로드**: {v.get('upload_date', today)} | **조회수**: {views} | **길이**: {v.get('duration') or '-'}",
                    "",
                ]
                if v.get("summary", "").strip():
                    for line in v["summary"].strip().split("\n"):
                        lines.append(f"> {line}")
                    lines.append("")
                f.write("\n".join(lines) + "\n")

    saved_urls = [v.get("url", "") for kw_vids in by_keyword.values() for v in kw_vids]
    logger.info(f"[keyword-async] 저장 완료: {file_path} ({total_new}개)")
    return {"saved": total_new, "skipped": len(videos) - total_new, "videoUrls": ",".join(saved_urls), "today": today}


async def _run_keyword_collect(params: KeywordCollectRequest):
    """키워드 수집 전체 파이프라인 (백그라운드에서 실행)."""
    kw_list = [k.strip() for k in params.keywords.split(",") if k.strip()]
    exclude_set = {v.strip() for v in params.exclude_ids.split(",") if v.strip()}
    all_videos: list[dict] = []

    async with httpx.AsyncClient() as client:
        for keyword in kw_list:
            try:
                videos = await _search_keyword_videos_api(
                    keyword, params.days, params.max_results, exclude_set, client,
                    params.include_subtitles, params.include_summary,
                )
                for v in videos:
                    all_videos.append(v)
                    exclude_set.add(v["video_id"])
            except Exception as e:
                logger.error(f"[keyword-async] '{keyword}' 실패: {e}")

    _write_keyword_md(all_videos, RESEARCH_BASE_DIR)


@app.get(
    "/research/collect-keywords-async",
    summary="키워드 수집 비동기 실행",
    description=(
        "키워드 수집을 백그라운드에서 실행하고 즉시 응답합니다. "
        "수집·자막·요약 완료 후 RESEARCH_BASE_DIR/키워드/YYYY-MM-DD.md 에 직접 저장됩니다."
    ),
)
async def collect_keywords_async(
    background_tasks: BackgroundTasks,
    keywords: str = Query(..., description="쉼표로 구분된 키워드 목록"),
    days: int = Query(default=30, ge=1, le=90),
    max_results: int = Query(default=5, ge=1, le=20),
    exclude_ids: str = Query(default=""),
    include_subtitles: bool = Query(default=False),
    include_summary: bool = Query(default=False),
):
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=500, detail="YOUTUBE_API_KEY 환경변수가 설정되지 않았습니다.")
    if not keywords.strip():
        logger.info("[keyword-async] 활성 키워드 없음 — 스킵")
        return {"status": "skipped", "keywords": [], "message": "활성화된 키워드가 없습니다."}

    params = KeywordCollectRequest(
        keywords=keywords,
        days=days,
        max_results=max_results,
        exclude_ids=exclude_ids,
        include_subtitles=include_subtitles,
        include_summary=include_summary,
    )
    background_tasks.add_task(_run_keyword_collect, params)
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    logger.info(f"[keyword-async] 백그라운드 시작: 키워드 {len(kw_list)}개")
    return {
        "status": "started",
        "keywords": kw_list,
        "message": f"키워드 {len(kw_list)}개 수집을 백그라운드에서 시작했습니다. 완료 후 키워드/ 폴더에 저장됩니다.",
    }


@app.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="YouTube 영상 → 텍스트 변환 (Whisper STT)",
    description=(
        "YouTube URL의 오디오를 다운로드하고 faster-whisper로 텍스트로 변환합니다. "
        "변환 결과는 output_dir에 .txt로 저장됩니다. 처리 시간이 길 수 있습니다 (영상 길이에 따라 수 분)."
    ),
)
async def transcribe_video(req: TranscribeRequest):
    if not _WHISPER_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="faster-whisper가 설치되지 않았습니다. pip install faster-whisper 를 실행하세요.",
        )

    output_dir = Path(req.output_dir).resolve() if req.output_dir else _DEFAULT_TRANSCRIPT_DIR

    valid_models = {"tiny", "base", "small", "medium", "large-v2", "large-v3"}
    if req.model not in valid_models:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 모델: {req.model}. 선택 가능: {valid_models}")

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            _run_transcribe,
            req.url,
            req.model,
            req.language,
            output_dir,
            req.keep_audio,
        )
        return TranscribeResponse(**result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"[Transcribe ERROR] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# 비디오 파이프라인 (STT + 요약 + Telegram 응답)
# ──────────────────────────────────────────────

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


def _update_md_with_summary(md_file: str, video_url: str, summary: str, mp3_url: str = "") -> bool:
    """기존 일별 .md 파일에서 해당 영상 항목을 찾아 요약과 MP3 링크를 삽입한다."""
    path = Path(md_file)
    if not path.exists():
        logger.warning(f"[BatchSTT] .md 파일 없음: {md_file}")
        return False

    content = path.read_text(encoding="utf-8")
    # video_id 추출 (watch?v=XXXXXXXXXXX 또는 youtu.be/XXXXXXXXXXX)
    m = re.search(r"(?:watch\?v=|youtu\.be/)([\w\-]{11})", video_url)
    if not m:
        logger.warning(f"[BatchSTT] video_id 추출 실패: {video_url}")
        return False
    video_id = m.group(1)

    lines = content.split("\n")
    insert_idx = None
    for i, line in enumerate(lines):
        if video_id in line:
            # URL 라인 이후 첫 번째 빈 줄 탐색 (메타데이터 끝) — 최대 10줄 탐색
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j].strip() == "":
                    insert_idx = j
                    break
            if insert_idx is None:
                # 빈 줄을 못 찾은 경우 디버그 로그
                snippet = repr(lines[i : min(i + 10, len(lines))])
                logger.warning(f"[BatchSTT] 삽입 위치 탐색 실패 (video_id={video_id}): {snippet}")
            break

    if insert_idx is None:
        return False

    # 이미 요약 또는 MP3 링크가 있으면 스킵
    for k in range(insert_idx, min(insert_idx + 4, len(lines))):
        if lines[k].startswith(">") or lines[k].startswith("🎵"):
            logger.info(f"[BatchSTT] 이미 요약 존재, 스킵: {video_id}")
            return False

    # 삽입 내용: MP3 링크 + 빈 줄 + 요약 blockquote
    insert_lines: list[str] = []
    if mp3_url:
        insert_lines.append(f"🎵 [MP3 다운로드]({mp3_url})")
        insert_lines.append("")
    insert_lines += [f"> {l}" for l in summary.strip().split("\n")]
    insert_lines.append("")

    lines = lines[:insert_idx] + insert_lines + lines[insert_idx:]
    path.write_text("\n".join(lines), encoding="utf-8")
    return True


async def _run_batch_stt(
    video_urls: list[str],
    md_file: str,
    model: str,
    notify_date: Optional[str] = None,
    notify_folder: str = "all",
) -> None:
    """백그라운드에서 영상 목록을 순서대로 STT → 요약 → .md 업데이트한다.
    notify_date가 지정되면 완료 후 notify_folder에 맞는 Telegram 알림을 자동 전송한다."""
    audio_dir = Path(AUDIO_DIR)
    loop = asyncio.get_event_loop()
    total = len(video_urls)
    logger.info(f"[BatchSTT] 시작: {total}개 영상, 모델={model}")

    async with httpx.AsyncClient() as client:
        for idx, url in enumerate(video_urls, 1):
            logger.info(f"[BatchSTT] ({idx}/{total}) {url}")
            try:
                # 1. 오디오 다운로드 (mp3는 AUDIO_DIR에 영구 보관)
                audio_path, metadata = await loop.run_in_executor(
                    _executor, prepare_audio, url, audio_dir
                )
                title = metadata.get("title", "video")
                mp3_url = f"{AUDIO_BASE_URL}/audio/{audio_path.name}"

                # 2. STT
                transcript, _ = await loop.run_in_executor(
                    _executor, transcribe, audio_path, model, "auto", None, 4
                )
                if not transcript:
                    logger.warning(f"[BatchSTT] 스크립트 없음: {url}")
                    continue

                # 3. Ollama 요약
                summary = await _summarize_with_ollama(transcript, client)
                if not summary:
                    continue

                # 4. .md 업데이트 (요약 + MP3 링크 삽입)
                updated = _update_md_with_summary(md_file, url, summary, mp3_url)
                logger.info(f"[BatchSTT] {'요약 삽입' if updated else '삽입 실패'}: {title}")

            except Exception as e:
                logger.error(f"[BatchSTT] 오류 ({url}): {type(e).__name__}: {e}")

    logger.info(f"[BatchSTT] 완료: {total}개 처리")

    # Telegram 알림 — notify_date가 지정된 경우에만 실행
    if notify_date and TELEGRAM_CHAT_ID:
        await _notify_daily(notify_date, TELEGRAM_CHAT_ID, notify_folder)


@app.get("/pipeline/batch-stt-async", summary="영상 목록 배치 STT + 요약 (비동기)")
async def pipeline_batch_stt_async(
    background_tasks: BackgroundTasks,
    video_urls: str  = Query(...,             description="처리할 YouTube URL 목록 (쉼표 구분)"),
    date:       str  = Query(...,             description="일별 .md 날짜 (YYYY-MM-DD)"),
    folder:     str  = Query(default="채널",  description="저장 폴더 (채널 | 키워드). RESEARCH_BASE_DIR/{folder}/{date}.md 에 요약 삽입"),
    notify:     bool = Query(default=False,   description="완료 후 Telegram 알림 전송 여부. True 시 TELEGRAM_CHAT_ID 환경변수 사용."),
):
    """
    영상 URL 목록을 받아 백그라운드에서 STT → Ollama 요약 후
    RESEARCH_BASE_DIR/{folder}/YYYY-MM-DD.md 에 요약을 삽입한다.
    notify=true 로 호출하면 모든 처리가 끝난 후 Telegram 알림을 자동 전송한다.
    Telegram 자격증명은 FastAPI 환경변수(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)에서만 읽으므로
    n8n에 Telegram 정보를 저장할 필요가 없다.
    """
    if not _PIPELINE_AVAILABLE:
        raise HTTPException(status_code=500, detail="video_pipeline.py를 찾을 수 없습니다.")

    url_list = [u.strip() for u in video_urls.split(",") if u.strip()]
    if not url_list:
        logger.info(f"[BatchSTT] 처리할 영상 없음 (video_urls 비어있음) — 스킵")
        return {"status": "skipped", "total": 0, "message": "처리할 신규 영상이 없습니다."}

    # FastAPI가 자신의 경로로 직접 구성
    md_file = str(Path(RESEARCH_BASE_DIR) / folder / f"{date}.md")
    logger.info(f"[BatchSTT] md_file 경로: {md_file}, 모델: {STT_MODEL}, 디바이스: {STT_DEVICE}")

    notify_date = date if notify else None
    background_tasks.add_task(_run_batch_stt, url_list, md_file, STT_MODEL, notify_date, folder)

    return {
        "status":      "started",
        "total":       len(url_list),
        "folder":      folder,
        "md_file":     md_file,
        "model":       STT_MODEL,
        "device":      STT_DEVICE,
        "notify":      notify,
        "message":     f"{len(url_list)}개 영상을 백그라운드에서 처리합니다."
                       + (" 완료 후 Telegram 알림을 전송합니다." if notify else ""),
    }


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


def _read_md_file(folder: str, date: str) -> tuple[str, int]:
    """RESEARCH_BASE_DIR/{folder}/{date}.md 를 읽고 (내용, 영상수)를 반환."""
    path = Path(RESEARCH_BASE_DIR) / folder / f"{date}.md"
    if not path.exists():
        return "", 0
    content = path.read_text(encoding="utf-8")
    # 영상 항목 수: 채널 MD는 "## [채널명]", 키워드 MD는 "### [채널명]" 형식
    count = sum(1 for line in content.splitlines()
                if line.startswith("## [") or line.startswith("### ["))
    return content, count


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


@app.get("/report/daily", response_class=HTMLResponse, summary="일일 수집 리포트 (HTML)")
async def report_daily(
    date: str = Query(default="", description="조회할 날짜 (YYYY-MM-DD). 생략 시 오늘."),
    folder: str = Query(default="all", description="채널 | 키워드 | all (기본: all)"),
):
    """
    RESEARCH_BASE_DIR/{folder}/{date}.md 를 모바일 친화적 HTML로 렌더링합니다.
    folder=all 이면 채널 + 키워드를 합쳐서 표시합니다.
    Telegram 인앱 브라우저에서 바로 열 수 있습니다.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    title = f"{date} 수집 리포트"

    if folder == "all":
        ch_content, ch_count = _read_md_file("채널", date)
        kw_content, kw_count = _read_md_file("키워드", date)
        combined = ""
        if ch_content:
            combined += ch_content
        if kw_content:
            combined += ("\n\n---\n\n" if combined else "") + kw_content
        return HTMLResponse(content=_render_md_html(title, combined))
    else:
        content, _ = _read_md_file(folder, date)
        return HTMLResponse(content=_render_md_html(title, content))


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


async def _notify_daily(date: str, chat_id: str, folder: str = "all") -> None:
    """수집 결과 요약을 Telegram으로 전송한다. 내부 공용 헬퍼.
    folder: "채널" | "키워드" | "all"
    """
    if folder == "채널":
        _, count = _read_md_file("채널", date)
        report_url = f"{FASTAPI_PUBLIC_URL}/report/daily?date={date}&folder=채널"
        lines = [
            f"📺 <b>{date} 채널 수집 완료</b>",
            "",
            f"영상 수: <b>{count}개</b>",
            "",
            f"📄 <a href='{report_url}'>채널 리포트 보기</a>",
        ]
        logger.info(f"[Report] Telegram 채널 알림 전송: {date} ({count}개)")
    elif folder == "키워드":
        _, count = _read_md_file("키워드", date)
        report_url = f"{FASTAPI_PUBLIC_URL}/report/daily?date={date}&folder=키워드"
        lines = [
            f"🔍 <b>{date} 키워드 수집 완료</b>",
            "",
            f"영상 수: <b>{count}개</b>",
            "",
            f"📄 <a href='{report_url}'>키워드 리포트 보기</a>",
        ]
        logger.info(f"[Report] Telegram 키워드 알림 전송: {date} ({count}개)")
    else:  # "all" — 기존 통합 리포트
        _, ch_count = _read_md_file("채널",  date)
        _, kw_count = _read_md_file("키워드", date)
        total_count = ch_count + kw_count
        report_url  = f"{FASTAPI_PUBLIC_URL}/report/daily?date={date}&folder=all"
        lines = [
            f"📊 <b>{date} 콘텐츠 수집 완료</b>",
            "",
            f"📺 채널 영상: <b>{ch_count}개</b>",
            f"🔍 키워드 영상: <b>{kw_count}개</b>",
            f"📦 총합: <b>{total_count}개</b>",
            "",
            f"📄 <a href='{report_url}'>리포트 보기</a>",
        ]
        logger.info(f"[Report] Telegram 통합 알림 전송: {date} (채널 {ch_count} + 키워드 {kw_count}개)")
    await _send_telegram(chat_id, "\n".join(lines))


@app.get("/report/notify-daily", summary="일일 수집 결과 Telegram 알림")
async def report_notify_daily(
    date: str = Query(default="", description="알림 대상 날짜 (YYYY-MM-DD). 생략 시 오늘."),
    chat_id: Optional[str] = Query(default=None, description="Telegram Chat ID (생략 시 환경변수 사용)"),
):
    """
    오늘의 채널/키워드 수집 결과 요약을 Telegram으로 전송하고
    HTML 리포트 링크를 함께 보냅니다.
    수동 호출 또는 테스트용. 자동화 파이프라인에서는 batch-stt-async의 notify=true 사용 권장.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not target_chat_id:
        raise HTTPException(status_code=400, detail="chat_id가 필요합니다.")
    await _notify_daily(date, target_chat_id)
    _, ch_count = _read_md_file("채널",  date)
    _, kw_count = _read_md_file("키워드", date)
    return {
        "status":     "sent",
        "date":       date,
        "ch_count":   ch_count,
        "kw_count":   kw_count,
        "total":      ch_count + kw_count,
        "report_url": f"{FASTAPI_PUBLIC_URL}/report/daily?date={date}&folder=all",
        "chat_id":    target_chat_id,
    }


# ──────────────────────────────────────────────
# Telegram 웹훅 관리
# ──────────────────────────────────────────────

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

def _get_collected_ids() -> set[str]:
    """수집 영상 폴더(채널 + 키워드)에서 기존 video_id를 모두 반환."""
    base = Path(RESEARCH_BASE_DIR)
    collected: set[str] = set()
    for folder in ["채널", "키워드"]:
        folder_path = base / folder
        if not folder_path.exists():
            continue
        for md_file in folder_path.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
                for m in re.finditer(r'watch\?v=([\w-]{11})', content):
                    collected.add(m.group(1))
            except Exception:
                pass
    return collected


def _parse_channel_config() -> tuple[list[str], int]:
    """모니터링 채널 목록.md 파싱 → (channel_urls, limit)."""
    config_file = Path(RESEARCH_BASE_DIR).parent / "모니터링 채널 목록.md"
    channel_urls: list[str] = []
    limit = 5
    try:
        for line in config_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^limit:\s*(\d+)', line)
            if m:
                limit = int(m.group(1))
            parts = [p.strip() for p in line.split("|")]
            # URL이 있는 컬럼 찾기 (채널 ID 컬럼 유무에 무관하게 동작)
            url = next((p for p in parts if p.startswith("https://")), None)
            if url and "✅" in parts:
                channel_urls.append(url)
    except Exception as e:
        logger.warning(f"[config] 채널 목록 읽기 실패: {e}")
    return channel_urls, limit


def _parse_keyword_config() -> tuple[list[str], int]:
    """모니터링 키워드 목록.md 파싱 → (keywords, limit)."""
    config_file = Path(RESEARCH_BASE_DIR).parent / "모니터링 키워드 목록.md"
    keywords: list[str] = []
    limit = 5
    try:
        for line in config_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^limit:\s*(\d+)', line)
            if m:
                limit = int(m.group(1))
            parts = [p.strip() for p in line.split("|")]
            if (len(parts) >= 6 and parts[5] == "✅"
                    and parts[1] and "키워드" not in parts[1]
                    and parts[2] in ("영어", "한국어")):
                keywords.append(parts[1])
    except Exception as e:
        logger.warning(f"[config] 키워드 목록 읽기 실패: {e}")
    return keywords, limit


def _write_channel_md(videos: list[dict], base_dir: str) -> dict:
    """채널 수집 결과를 MD 파일에 저장. 중복 체크 포함."""
    base = Path(base_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = base / "채널" / f"{today}.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    collected_ids = _get_collected_ids()

    if not file_path.exists():
        channel_count = len({v.get("channel_name", "") for v in videos})
        file_path.write_text(
            f"# 채널 모니터링 — {today}\n\n수집: {len(videos)}개 영상 | 채널: {channel_count}개\n",
            encoding="utf-8",
        )

    saved_urls: list[str] = []
    skipped = 0

    with file_path.open("a", encoding="utf-8") as f:
        for v in videos:
            vid = v.get("video_id", "")
            if not vid or vid in collected_ids:
                skipped += 1
                continue

            views = f"{v['view_count']:,}" if v.get("view_count") else "-"
            thumb = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
            url   = v.get("url", "")

            lines = ["", "---", "", f"## [{v.get('channel_name', '')}] {v.get('title', '')}", ""]
            lines.append(f"![썸네일]({thumb})")
            lines.append("")
            lines.append(f"**URL**: {url}  ")
            lines.append(
                f"**업로드**: {v.get('upload_date', today)} | "
                f"**조회수**: {views} | **길이**: {v.get('duration') or '-'}"
            )
            lines.append("")

            f.write("\n".join(lines) + "\n")
            saved_urls.append(url)
            collected_ids.add(vid)

    logger.info(f"[save-channel-md] 저장 {len(saved_urls)}개 / 스킵 {skipped}개 → 채널/{today}.md")
    return {
        "saved":     len(saved_urls),
        "skipped":   skipped,
        "mdFile":    str(file_path),
        "videoUrls": ",".join(saved_urls),
        "today":     today,
    }


# ──────────────────────────────────────────────
# /research/config  /research/save-*-md  엔드포인트
# ──────────────────────────────────────────────

@app.get(
    "/research/config",
    summary="수집 설정 + 기존 수집 ID 반환 (n8n node-prepare 대체)",
)
async def research_config():
    """
    모니터링 채널 목록.md / 모니터링 키워드 목록.md 를 읽고,
    수집 영상 폴더를 스캔해 기존 video_id 목록을 반환합니다.
    n8n의 '채널 목록 + 기존 ID 수집' Code 노드를 대체합니다.
    """
    if not RESEARCH_BASE_DIR:
        raise HTTPException(status_code=500, detail="RESEARCH_BASE_DIR 환경변수가 설정되지 않았습니다.")

    channel_urls, channel_limit = _parse_channel_config()
    keywords, keyword_limit     = _parse_keyword_config()
    collected_ids               = _get_collected_ids()

    logger.info(
        f"[config] 채널 {len(channel_urls)}개 (limit={channel_limit}), "
        f"키워드 {len(keywords)}개 (limit={keyword_limit}), "
        f"기존 수집 {len(collected_ids)}개"
    )
    return {
        "channelUrls":    ",".join(channel_urls),
        "channelLimit":   str(channel_limit),
        "keywords":       ",".join(keywords),
        "keywordLimit":   str(keyword_limit),
        "excludeIds":     ",".join(collected_ids),
        "collectedCount": len(collected_ids),
    }


class SaveChannelMdRequest(BaseModel):
    videos: list[dict]  # RecentVideoItem 구조


class SaveKeywordMdRequest(BaseModel):
    videos: list[dict]  # KeywordVideoItem 구조


@app.post(
    "/research/save-channel-md",
    summary="채널 수집 결과 MD 저장 (n8n node-save 대체)",
)
async def save_channel_md(request: Request):
    """
    /research/recent-videos 응답의 videos 배열을 받아
    RESEARCH_BASE_DIR/채널/YYYY-MM-DD.md 에 저장합니다.
    n8n body 포맷에 무관하게 동작하도록 Request로 직접 파싱합니다.
    """
    if not RESEARCH_BASE_DIR:
        raise HTTPException(status_code=500, detail="RESEARCH_BASE_DIR 환경변수가 설정되지 않았습니다.")

    try:
        body = await request.json()
    except Exception:
        body = {}

    videos = body.get("videos", [])
    if not isinstance(videos, list):
        videos = []

    result = _write_channel_md(videos, RESEARCH_BASE_DIR)
    return result


@app.post(
    "/research/save-keyword-md",
    summary="키워드 수집 결과 MD 저장 (n8n node-keyword-save 대체)",
)
async def save_keyword_md(request: Request):
    """
    /research/keyword-videos 응답의 videos 배열을 받아
    RESEARCH_BASE_DIR/키워드/YYYY-MM-DD.md 에 저장합니다.
    n8n body 포맷에 무관하게 동작하도록 Request로 직접 파싱합니다.
    """
    if not RESEARCH_BASE_DIR:
        raise HTTPException(status_code=500, detail="RESEARCH_BASE_DIR 환경변수가 설정되지 않았습니다.")

    try:
        body = await request.json()
    except Exception:
        body = {}

    videos = body.get("videos", [])
    if not isinstance(videos, list):
        videos = []

    result = _write_keyword_md(videos, RESEARCH_BASE_DIR)
    today = datetime.now().strftime("%Y-%m-%d")
    if result is None:
        result = {"saved": 0, "skipped": 0, "videoUrls": "", "today": today}
    return result


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
