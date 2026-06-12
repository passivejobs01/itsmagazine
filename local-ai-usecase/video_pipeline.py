"""
YouTube URL 또는 로컬 파일 → STT → Ollama 요약 → .md 저장 파이프라인

사용 예시:
    python video_pipeline.py "https://www.youtube.com/watch?v=XXXX"
    python video_pipeline.py "https://www.youtube.com/watch?v=XXXX" --model medium
    python video_pipeline.py "D:/videos/sample.mp4"
    python video_pipeline.py "https://www.youtube.com/watch?v=XXXX" --no-summary
    python video_pipeline.py "https://www.youtube.com/watch?v=XXXX" --device cpu
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv

# faster-whisper는 STT 시점에만 필요하므로 지연 import한다.
# (이 모듈의 유틸·프롬프트만 재사용하는 쪽에서는 미설치여도 import 가능)

load_dotenv()

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
OLLAMA_HOST    = os.getenv("OLLAMA_HOST",    "http://localhost:11434")
AUDIO_DIR      = os.getenv("AUDIO_DIR",      str(Path(__file__).parent / "audio"))
AUDIO_BASE_URL = os.getenv("AUDIO_BASE_URL", "http://localhost:8003")

# 요약(map-reduce) 설정 — fastapi_linux와 동일 기본값. 품질 우선 gemma4:12b.
SUMMARY_MODEL       = os.getenv("SUMMARY_MODEL",       "gemma4:12b")
SUMMARY_NUM_CTX     = int(os.getenv("SUMMARY_NUM_CTX",     "16384"))
SUMMARY_CHUNK_CHARS = int(os.getenv("SUMMARY_CHUNK_CHARS", "4000"))

# 요약 엔진(map-reduce) — summarize_transcript를 단일 소스로 재사용. 없으면 단일 호출 폴백.
try:
    from summarize_transcript import summarize as _summarize_text, PROMPTS as _SUMMARY_PROMPTS
    _SUMMARIZER_AVAILABLE = True
except ImportError:
    _SUMMARIZER_AVAILABLE = False

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    """파일시스템 저장용 파일명 (한글·영문 유지, 특수문자 제거)."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip().strip(".")[:150] or "video"


def sanitize_audio_filename(name: str) -> str:
    """URL-safe mp3 파일명 생성.

    - 이모지·특수 유니코드 심볼 제거
    - 공백 및 URL 비안전 문자 → 언더스코어(_)
    - 한글·영문·숫자·하이픈·언더스코어만 유지
    - 연속 언더스코어 정리
    예: "홈서버 구축하기 🏠 - Proxmox 설치" → "홈서버_구축하기_-_Proxmox_설치"
    """
    # 이모지 및 Symbol 계열 유니코드 제거
    cleaned = ""
    for ch in name:
        cat = unicodedata.category(ch)
        if cat.startswith("S") or cat.startswith("M"):
            continue
        cleaned += ch

    # URL 비안전 문자(공백 포함)를 언더스코어로
    cleaned = re.sub(r'[^\w가-힣ㄱ-ㅎㅏ-ㅣ\-]', "_", cleaned)
    # 연속 언더스코어 정리
    cleaned = re.sub(r'_+', "_", cleaned)
    cleaned = cleaned.strip("_").strip("-")

    return (cleaned[:120] or "audio")


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "www."))


# ──────────────────────────────────────────────
# 1단계: 오디오 준비
# ──────────────────────────────────────────────
def download_audio(url: str, audio_dir: Path) -> tuple[Path, dict]:
    """yt-dlp로 YouTube 오디오를 mp3로 다운로드.

    파일명은 URL-safe로 직접 지정해 저장한다.
    """
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 먼저 메타데이터만 추출해 제목을 확보
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get("title", "video")
        metadata = {
            "title":       title,
            "url":         info.get("webpage_url") or url,
            "channel":     info.get("channel") or info.get("uploader") or "",
            "channel_url": info.get("channel_url") or info.get("uploader_url") or "",
        }

    safe_name  = sanitize_audio_filename(title)
    audio_path = audio_dir / f"{safe_name}.mp3"

    # 이미 존재하면 재다운로드 생략
    if audio_path.exists():
        print(f"[CACHE] 이미 존재: {audio_path.name}")
        return audio_path, metadata

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(audio_path.with_suffix("")),  # 확장자 제외한 경로 지정
        "quiet": False,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    if not audio_path.exists():
        raise FileNotFoundError(f"오디오 추출 실패: {audio_path}")

    return audio_path, metadata


def extract_audio_from_video(video_path: Path, audio_dir: Path) -> Path:
    """ffmpeg로 로컬 비디오에서 mp3를 추출."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    safe_name  = sanitize_audio_filename(video_path.stem)
    audio_path = audio_dir / f"{safe_name}.mp3"
    print(f"[FFMPEG] 오디오 추출: {video_path.name} → {audio_path.name}")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        "-loglevel", "error", str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패:\n{result.stderr}")
    return audio_path


def prepare_audio(input_source: str, audio_dir: Path) -> tuple[Path, dict]:
    """입력 소스로부터 오디오 파일을 준비한다. mp3는 항상 audio_dir에 유지."""
    if is_url(input_source):
        return download_audio(input_source, audio_dir)

    src = Path(input_source).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {src}")

    ext      = src.suffix.lower()
    metadata = {"title": src.stem, "url": "", "channel": "", "channel_url": ""}

    if ext in AUDIO_EXTS:
        print(f"[INPUT] 오디오 파일: {src.name}")
        return src, metadata

    if ext in VIDEO_EXTS:
        audio_path = extract_audio_from_video(src, audio_dir)
        return audio_path, metadata

    raise ValueError(
        f"지원하지 않는 형식: {ext}\n"
        f"  비디오: {', '.join(sorted(VIDEO_EXTS))}\n"
        f"  오디오: {', '.join(sorted(AUDIO_EXTS))}"
    )


# ──────────────────────────────────────────────
# 2단계: STT
# ──────────────────────────────────────────────
def detect_device() -> tuple[str, str]:
    """CUDA 가용 여부에 따라 device와 compute_type을 반환."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


# (model_size, device, compute_type) → WhisperModel 캐시.
# 동일 설정으로 재호출 시 모델을 재로딩하지 않는다 (서버에서 반복 STT 시 핵심).
_MODEL_CACHE: dict = {}


def _get_cached_model(model_size: str, device: str, compute_type: str, cpu_threads: int):
    """Whisper 모델을 캐시에서 가져오거나 새로 로드한다 (faster-whisper 지연 import)."""
    key = (model_size, device, compute_type)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    from faster_whisper import WhisperModel
    print(f"[STT] 모델 로딩: {model_size}  device={device}  compute={compute_type}")
    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
    )
    _MODEL_CACHE[key] = model
    return model


def transcribe(
    audio_path: Path,
    model_size: str = "small",
    device: str = "auto",
    language: str | None = None,
    cpu_threads: int = 4,
) -> tuple[str, str]:
    """faster-whisper로 STT를 수행한다. 반환: (transcript_text, detected_language)"""
    if device == "auto":
        device, compute_type = detect_device()
    else:
        compute_type = "float16" if device == "cuda" else "int8"

    model = _get_cached_model(model_size, device, compute_type, cpu_threads)

    print(f"[STT] 변환 시작: {audio_path.name}")
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
    )

    detected = info.language
    print(f"[STT] 감지 언어: {detected}  (확률 {info.language_probability:.2f})")

    lines: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(text)
            print(f"  [{seg.start:7.2f}s → {seg.end:7.2f}s] {text}")

    return "\n".join(lines), detected


def free_whisper_models() -> None:
    """캐시된 whisper 모델을 해제해 GPU VRAM을 반환한다.

    단일 GPU를 Ollama 요약과 공유할 때, STT 직후 호출하면 whisper가 점유하던
    VRAM이 풀려 요약(LLM)이 GPU에 온전히 올라갈 수 있다(부분 CPU 오프로드 방지).
    """
    import gc
    if not _MODEL_CACHE:
        return
    _MODEL_CACHE.clear()
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ──────────────────────────────────────────────
# 3단계: Ollama 요약
# ──────────────────────────────────────────────
def check_ollama() -> bool:
    """Ollama 서버 연결 여부를 확인한다."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def build_summary_prompt(transcript: str, title: str = "") -> str:
    """Ollama 요약용 프롬프트를 생성한다 (동기·비동기 요약 공용 단일 소스).

    title이 주어지면 영상 제목을 프롬프트에 포함한다.
    """
    intro = (
        f"다음은 유튜브 영상 '{title}'의 자막입니다. "
        if title else
        "다음은 유튜브 영상의 자막입니다. "
    )
    return (
        intro +
        "아래 형식으로 한국어로 영상 내용을 정리해주세요. 영상의 전반적인 내용을 빠짐없이 그리고  이해가 쉽도록 정리하세요. /no_think\n\n"
        "형식:\n"
        "📌 **핵심 주제**: (한 문장으로 이 영상이 다루는 주제)\n\n"
        "📋 **주요 내용**:\n"
        " 🔹 (첫 번째 단락)\n"
        " 🔹 (두 번째 단락)\n"
        " 🔹 (세 번째 단락)\n"
        " 🔹 (추가 단락, 필요한 만큼)\n\n"
        "💡 **핵심 포인트**: (시청자가 얻어갈 수 있는 가장 중요한 인사이트를 충실하게 정리)\n\n"
        f"자막:\n{transcript[:5000].strip()}\n\n정리 결과:"
    )

def summarize_with_ollama(transcript: str, title: str) -> str:
    """Ollama로 한국어 정리 노트를 생성한다 (map-reduce).

    긴 자막도 잘리지 않도록 summarize_transcript의 청크 map→reduce 엔진을 재사용한다.
    모델은 SUMMARY_MODEL(기본 gemma4:12b), 서버 로컬 OLLAMA_HOST 사용.
    """
    if _SUMMARIZER_AVAILABLE:
        return _summarize_text(
            transcript, title,
            host=OLLAMA_HOST, model=SUMMARY_MODEL,
            final_template=_SUMMARY_PROMPTS["detailed"],
            chunk_chars=SUMMARY_CHUNK_CHARS, num_ctx=SUMMARY_NUM_CTX,
            temperature=0.3,
        )

    # 폴백: 요약 엔진 모듈이 없으면 단일 호출(앞부분만)
    prompt = build_summary_prompt(transcript, title)
    payload = json.dumps({
        "model":   SUMMARY_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.3},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    print(f"[OLLAMA] 요약 요청(단일·폴백) → {OLLAMA_HOST}  model={SUMMARY_MODEL}")
    with urllib.request.urlopen(req, timeout=120) as r:
        result = json.load(r)
    return result.get("response", "").strip()


# ──────────────────────────────────────────────
# 4단계: .md 저장
# ──────────────────────────────────────────────
def save_markdown(
    output_dir: Path,
    metadata: dict,
    audio_path: Path,
    transcript: str,
    language: str,
    summary: str,
    model_size: str,
    base_url: str | None = None,
) -> Path:
    title    = metadata.get("title", "video")
    url      = metadata.get("url", "")
    channel  = metadata.get("channel", "")
    today    = datetime.now().strftime("%Y-%m-%d")
    out_path = output_dir / f"{sanitize_filename(title)}.md"

    # mp3 공개 URL 생성 (호출자가 base_url을 주면 그 값, 없으면 모듈 기본값)
    mp3_url = f"{(base_url or AUDIO_BASE_URL).rstrip('/')}/audio/{audio_path.name}"

    lines = [
        f"# {title}",
        "",
        f"- **처리일**: {today}",
        f"- **STT 모델**: whisper {model_size}",
        f"- **언어**: {language}",
    ]
    if url:
        lines.append(f"- **URL**: {url}")
    if channel:
        lines.append(f"- **채널**: {channel}")
    lines.append(f"- **MP3**: [{audio_path.name}]({mp3_url})")

    if summary:
        lines += ["", "## 요약", "", summary]

    lines += ["", "## 전체 스크립트", "", transcript]

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube URL 또는 로컬 파일 → STT → Ollama 요약 → .md 저장",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="YouTube URL 또는 로컬 파일 경로")
    parser.add_argument(
        "--model", default="small",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper 모델 크기 (기본: small)",
    )
    parser.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda"],
        help="추론 장치 (기본: auto — CUDA 우선)",
    )
    parser.add_argument(
        "--language", default=None,
        help="언어 코드 (예: en, ko). 미지정 시 자동 감지",
    )
    parser.add_argument(
        "--cpu-threads", type=int, default=4,
        help="CPU 스레드 수 (device=cpu일 때 유효, 기본: 4)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="결과 .md 저장 디렉토리 (기본: content_tools/summaries/)",
    )
    parser.add_argument(
        "--audio-dir", default=None,
        help="mp3 저장 디렉토리 (기본: AUDIO_DIR 환경변수 또는 content_tools/audio/)",
    )
    parser.add_argument(
        "--no-summary", action="store_true",
        help="Ollama 요약 생략 (STT 결과만 저장)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    script_dir = Path(__file__).parent
    output_dir = Path(args.output_dir).resolve() if args.output_dir else script_dir / "summaries"
    audio_dir  = Path(args.audio_dir).resolve()  if args.audio_dir  else Path(AUDIO_DIR)

    try:
        # 1. 오디오 준비
        print(f"\n{'='*60}")
        print(f"[INPUT] {args.input}")
        audio_path, metadata = prepare_audio(args.input, audio_dir)
        title = metadata.get("title", "video")
        print(f"[READY] {audio_path.name}")

        # 2. STT
        print(f"\n{'='*60}")
        transcript, language = transcribe(
            audio_path,
            model_size=args.model,
            device=args.device,
            language=args.language,
            cpu_threads=args.cpu_threads,
        )
        print(f"[STT] 완료: {len(transcript)}자")

        # 3. Ollama 요약
        summary = ""
        if not args.no_summary:
            print(f"\n{'='*60}")
            if check_ollama():
                try:
                    summary = summarize_with_ollama(transcript, title)
                except Exception as e:
                    print(f"[OLLAMA] 요약 실패 (스킵): {type(e).__name__}: {e}")
            else:
                print(f"[OLLAMA] 서버 연결 실패 ({OLLAMA_HOST}) — 요약 생략")

        # 4. .md 저장
        print(f"\n{'='*60}")
        out_path = save_markdown(
            output_dir, metadata, audio_path,
            transcript, language, summary, args.model,
        )
        print(f"[SAVE] {out_path}")
        print(f"[MP3 ] {AUDIO_BASE_URL}/audio/{audio_path.name}")

        print(f"\n{'='*60}")
        print("완료!")
        return 0

    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
