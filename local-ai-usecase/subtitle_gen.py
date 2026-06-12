"""
유튜브 콘텐츠 자막 생성기 (faster-whisper 단어 타임스탬프 기반 캡션 재분할)

YouTube URL 또는 로컬 오디오/영상 → STT(단어 단위 타임스탬프) → 캡션 분할 → SRT 저장.
DaVinci Resolve "자동 자막" 설정과 동일한 옵션을 제공한다.

옵션 (이미지 설정 매핑)
─────────────────────
  --max-chars   최대 길이(자)        : 한 "줄"의 최대 글자 수 (기본 25, 두 줄이면 캡션 전체 최대 ~2배)
  --min-duration 최소 기간(초)       : 캡션 최소 표시 시간 (기본 3.0)
  --gap-frames  캡션 사이의 간격(프레임): 연속 캡션 사이 최소 간격 (기본 0). --fps로 초 변환
  --lines       한 줄 / 두 줄         : 1 또는 2 (기본 1)
  --fps         프레임 간격 계산용 FPS (기본 30)

사용법:
    python subtitle_gen.py "https://www.youtube.com/watch?v=XXXX"
    python subtitle_gen.py "projects/.../02_audio/voice.wav" --lines 1 --max-chars 36
    python subtitle_gen.py "video.mp4" --min-duration 2.5 --gap-frames 2 --fps 30
    python subtitle_gen.py "voice.wav" -o "voice.srt"

설치:
    pip install -U qwen-tts soundfile   # (이미 설치된 프로젝트 의존성 사용: faster-whisper, yt-dlp)
"""

import argparse
import sys
from pathlib import Path

from video_pipeline import VIDEO_EXTS, is_url, prepare_audio, sanitize_filename


# 문장 종결로 간주해 캡션을 끊는 문자
_SENT_END = set(".?!…。！？")


# ──────────────────────────────────────────────
# SRT 직렬화
# ──────────────────────────────────────────────
def to_srt_time(seconds: float) -> str:
    """초(float) → SRT 타임스탬프 HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ──────────────────────────────────────────────
# STT — 단어 단위 타임스탬프
# ──────────────────────────────────────────────
def transcribe_words(audio_path: Path, model_size: str, language: str) -> list[dict]:
    """faster-whisper로 STT → 단어 리스트 [{text, start, end}] 반환."""
    from faster_whisper import WhisperModel

    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    print(f"[STT] 모델: {model_size}  장치: {device}  언어: {language}")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print(f"[STT] 변환 중: {audio_path.name}")
    segments_gen, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    print(f"[STT] 감지 언어: {info.language} ({info.language_probability:.0%})  길이: {info.duration:.1f}초\n")

    words: list[dict] = []
    for seg in segments_gen:
        for w in (seg.words or []):
            t = (w.word or "").strip()
            if not t:
                continue
            words.append({"text": t, "start": round(w.start, 3), "end": round(w.end, 3)})
    return words


# ──────────────────────────────────────────────
# 줄바꿈 / 캡션 분할
# ──────────────────────────────────────────────
def _greedy_lines(tokens: list[str], max_chars: int) -> list[str]:
    """토큰을 한 줄 max_chars 이하로 순차 채워 줄 목록을 만든다."""
    lines: list[str] = []
    cur = ""
    for t in tokens:
        if not cur:
            cur = t
        elif len(cur) + 1 + len(t) <= max_chars:
            cur = f"{cur} {t}"
        else:
            lines.append(cur)
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _lines_needed(tokens: list[str], max_chars: int) -> int:
    """이 토큰들을 max_chars 폭으로 채울 때 필요한 줄 수."""
    return len(_greedy_lines(tokens, max_chars)) or 1


def wrap_caption(tokens: list[str], max_chars: int, lines: int) -> list[str]:
    """캡션 토큰을 lines줄 이하로 줄바꿈한다 (두 줄이면 길이 균형 분할)."""
    full = " ".join(tokens)
    if lines <= 1 or len(tokens) == 1 or len(full) <= max_chars:
        return [full]

    # 두 줄: 각 줄 max_chars 이하가 되는 분할 중 두 줄 길이 차가 최소인 지점 선택
    best = None
    for i in range(1, len(tokens)):
        l1 = " ".join(tokens[:i])
        l2 = " ".join(tokens[i:])
        if len(l1) <= max_chars and len(l2) <= max_chars:
            diff = abs(len(l1) - len(l2))
            if best is None or diff < best[0]:
                best = (diff, l1, l2)
    if best:
        return [best[1], best[2]]
    # 균형 분할 실패(긴 토큰 등) → 순차 채움 폴백
    return _greedy_lines(tokens, max_chars)


def build_captions(words: list[dict], max_chars: int, lines: int) -> list[dict]:
    """단어 리스트를 (줄당 max_chars × lines줄) 한도와 문장 경계로 캡션으로 묶는다.
    반환: [{start, end, text}] (text는 줄바꿈 \\n 포함)."""
    captions: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            tokens = [w["text"] for w in cur]
            captions.append({
                "start": cur[0]["start"],
                "end":   cur[-1]["end"],
                "text":  "\n".join(wrap_caption(tokens, max_chars, lines)),
            })

    for w in words:
        tentative = [x["text"] for x in cur] + [w["text"]]
        if _lines_needed(tentative, max_chars) <= lines:
            cur.append(w)
        else:
            flush()
            cur = [w]
        # 문장 종결부호로 끝나면 캡션 종료
        if cur and cur[-1]["text"][-1] in _SENT_END:
            flush()
            cur = []
    flush()
    return captions


# ──────────────────────────────────────────────
# 타이밍 보정 (최소 기간 + 캡션 사이 간격)
# ──────────────────────────────────────────────
def adjust_timing(captions: list[dict], min_duration: float, gap_sec: float) -> list[dict]:
    """최소 표시 시간 확보 후, 연속 캡션이 겹치지 않도록 간격을 적용한다."""
    # 1) 최소 기간 확보 (끝 시간 연장)
    for c in captions:
        if c["end"] - c["start"] < min_duration:
            c["end"] = c["start"] + min_duration

    # 2) 겹침/간격 해소 (다음 캡션 시작을 침범하지 않도록 이전 끝을 자름)
    for i in range(len(captions) - 1):
        max_end = captions[i + 1]["start"] - gap_sec
        if captions[i]["end"] > max_end:
            captions[i]["end"] = max(captions[i]["start"] + 0.001, round(max_end, 3))
    return captions


def save_srt(captions: list[dict], out_path: Path) -> None:
    lines: list[str] = []
    for i, c in enumerate(captions, 1):
        lines.append(str(i))
        lines.append(f"{to_srt_time(c['start'])} --> {to_srt_time(c['end'])}")
        lines.append(c["text"])
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SRT] 저장: {out_path}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="유튜브 콘텐츠 자막 생성 (faster-whisper → SRT)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", help="YouTube URL 또는 로컬 오디오/영상 파일 경로")
    p.add_argument("--output", "-o", default=None, help="출력 SRT 경로 (기본: 입력 폴더/입력이름.srt)")
    p.add_argument("--output-dir", default=None, help="출력 디렉토리 (--output 미지정 시)")
    # 이미지 설정
    p.add_argument("--max-chars", type=int, default=25, help="최대 길이(자) - 한 줄 최대 글자 수 (기본 25)")
    p.add_argument("--min-duration", type=float, default=3.0, help="최소 기간(초) (기본 3.0)")
    p.add_argument("--gap-frames", type=int, default=0, help="캡션 사이의 간격(프레임) (기본 0)")
    p.add_argument("--lines", type=int, default=1, choices=[1, 2], help="한 줄(1) / 두 줄(2) (기본 1)")
    p.add_argument("--fps", type=float, default=30.0, help="프레임↔초 변환용 FPS (기본 30)")
    # STT
    p.add_argument("--model", default="large-v3",
                   choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                   help="Whisper 모델 (기본: large-v3)")
    p.add_argument("--language", default="ko", help="언어 코드 (기본: ko)")
    p.add_argument("--keep-audio", action="store_true",
                   help="URL/영상에서 추출한 오디오를 삭제하지 않고 보관")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    src_is_url = is_url(args.input)
    local_path = None if src_is_url else Path(args.input).expanduser().resolve()
    if not src_is_url and (local_path is None or not local_path.exists()):
        print(f"[ERROR] 파일 없음: {args.input}", file=sys.stderr)
        return 1

    # 출력 디렉토리 결정
    if args.output_dir:
        out_dir = Path(args.output_dir).resolve()
    elif local_path is not None:
        out_dir = local_path.parent
    else:
        out_dir = Path(__file__).parent / "subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 우리가 새로 만든(다운로드/추출) 오디오인지 — 끝나고 정리 여부 판단
    created_audio = src_is_url or (
        local_path is not None and local_path.suffix.lower() in VIDEO_EXTS
    )

    gap_sec = args.gap_frames / args.fps if args.fps > 0 else 0.0

    try:
        # 1. 오디오 준비 (URL 다운로드 / 영상 추출 / 오디오 그대로)
        audio_path, metadata = prepare_audio(args.input, out_dir)

        # 출력 SRT 이름: 로컬 입력은 원본 파일명과 동일하게(비디오 옆에 동일 이름.srt),
        #               URL은 영상 제목을 파일명으로 정리해서 사용
        if local_path is not None:
            stem = local_path.stem
        else:
            stem = sanitize_filename(metadata.get("title") or audio_path.stem)

        srt_path = Path(args.output).resolve() if args.output else out_dir / f"{stem}.srt"

        # 2. STT (단어 타임스탬프)
        words = transcribe_words(audio_path, args.model, args.language)
        if not words:
            print("[ERROR] STT 결과가 없습니다.", file=sys.stderr)
            return 1

        # 3. 캡션 분할 + 타이밍 보정
        captions = build_captions(words, args.max_chars, args.lines)
        captions = adjust_timing(captions, args.min_duration, gap_sec)

        # 미리보기
        print(f"[자막] 단어 {len(words)}개 → 캡션 {len(captions)}개 "
              f"(최대 {args.max_chars}자 × {args.lines}줄, 최소 {args.min_duration}초, "
              f"간격 {args.gap_frames}프레임@{args.fps:g}fps)\n")
        for i, c in enumerate(captions[:8], 1):
            preview = c["text"].replace("\n", " / ")
            print(f"  {i:>2} [{c['start']:6.2f}→{c['end']:6.2f}]  {preview}")
        if len(captions) > 8:
            print(f"  ... (총 {len(captions)}개)")

        # 4. 저장
        save_srt(captions, srt_path)

        # 5. 임시 오디오 정리
        if created_audio and not args.keep_audio:
            try:
                audio_path.unlink()
            except OSError:
                pass

        print(f"\n✅ 완료: {srt_path}")
        return 0

    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
