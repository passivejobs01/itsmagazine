"""
YouTube 영상 또는 로컬 동영상/오디오 파일을 텍스트 스크립트로 변환하는 CLI 도구.

사용 예시:
    # YouTube
    python youtube_transcriber.py https://www.youtube.com/watch?v=XXXX

    # 로컬 비디오
    python youtube_transcriber.py "D:/videos/sample.mp4"
    python youtube_transcriber.py "C:/clips/lecture.mkv" --model medium

    # 로컬 오디오
    python youtube_transcriber.py "D:/audio/podcast.mp3"

    # 옵션
    python youtube_transcriber.py <input> --output-dir ./transcripts --language ko --keep-audio
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel


VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip().strip(".")[:150] or "video"


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "www."))


def download_audio(url: str, output_dir: Path) -> tuple[Path, str, dict[str, str]]:
    """yt-dlp로 오디오만 다운로드하고 mp3로 추출한다.

    Returns:
        (audio_path, title, metadata)
        metadata: {"url", "channel", "channel_url"} — 헤더 기록용
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(output_dir / "%(title)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": False,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "video")
        metadata = {
            "url": info.get("webpage_url") or url,
            "channel": info.get("channel") or info.get("uploader") or "",
            "channel_url": info.get("channel_url") or info.get("uploader_url") or "",
        }

    safe_title = sanitize_filename(title)
    audio_path = output_dir / f"{safe_title}.mp3"

    if not audio_path.exists():
        for f in output_dir.glob("*.mp3"):
            if f.stat().st_mtime > 0:
                audio_path = f
                break

    if not audio_path.exists():
        raise FileNotFoundError(f"오디오 추출 실패: {audio_path}")

    return audio_path, title, metadata


def extract_audio_from_video(video_path: Path, output_dir: Path) -> Path:
    """ffmpeg로 비디오에서 오디오 트랙을 mp3로 추출."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(video_path.stem)
    audio_path = output_dir / f"{safe_title}.mp3"

    print(f"[FFMPEG] 오디오 추출: {video_path.name} -> {audio_path.name}")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn",                      # 비디오 트랙 제외
        "-acodec", "libmp3lame",
        "-q:a", "2",                # VBR 고품질
        "-loglevel", "error",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 추출 실패:\n{result.stderr}")

    if not audio_path.exists():
        raise FileNotFoundError(f"추출 결과 파일이 없습니다: {audio_path}")
    return audio_path


def prepare_audio(
    input_source: str,
    output_dir: Path,
    extract_video: bool = False,
) -> tuple[Path, str, bool, dict[str, str]]:
    """입력 소스(URL/비디오/오디오)로부터 변환에 사용할 미디어 파일을 준비.

    Args:
        extract_video: True면 비디오 → mp3 사전 추출. False면 비디오를 그대로 전달
            (faster-whisper가 내부 PyAV로 오디오 스트림만 디코딩, 훨씬 빠름).

    Returns:
        (media_path, title, is_temp, metadata)
        is_temp: 변환 후 자동 삭제 가능 여부 (다운로드 또는 추출한 파일이면 True)
        metadata: YouTube 입력일 때만 url/channel 포함, 로컬 파일이면 빈 dict
    """
    if is_url(input_source):
        audio_path, title, metadata = download_audio(input_source, output_dir)
        return audio_path, title, True, metadata

    src = Path(input_source).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {src}")
    if not src.is_file():
        raise ValueError(f"파일이 아닙니다: {src}")

    ext = src.suffix.lower()
    title = src.stem

    if ext in AUDIO_EXTS:
        print(f"[INPUT] 오디오 파일 직접 사용: {src.name}")
        return src, title, False, {}
    if ext in VIDEO_EXTS:
        if extract_video:
            audio_path = extract_audio_from_video(src, output_dir)
            return audio_path, title, True, {}
        print(f"[INPUT] 비디오 파일 직접 전달 (사전 mp3 추출 생략): {src.name}")
        return src, title, False, {}

    raise ValueError(
        f"지원하지 않는 파일 형식: {ext}\n"
        f"  지원 비디오: {', '.join(sorted(VIDEO_EXTS))}\n"
        f"  지원 오디오: {', '.join(sorted(AUDIO_EXTS))}"
    )


def transcribe_audio(
    audio_path: Path,
    model_size: str = "large-v3",
    language: str | None = None,
    device: str = "auto",
) -> tuple[str, str]:
    """faster-whisper로 STT 수행. (transcript_text, detected_language) 반환."""
    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    compute_type = "float16" if device == "cuda" else "int8"
    print(f"[STT] 모델 로딩: {model_size} (device={device}, compute={compute_type})")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print(f"[STT] 변환 시작: {audio_path.name}")
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
    )

    detected = info.language
    print(f"[STT] 감지된 언어: {detected} (확률 {info.language_probability:.2f})")

    lines: list[str] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(text)
            print(f"  [{seg.start:7.2f}s -> {seg.end:7.2f}s] {text}")

    return "\n".join(lines), detected


def save_transcript(
    output_dir: Path,
    title: str,
    transcript: str,
    language: str,
    metadata: dict[str, str] | None = None,
) -> Path:
    safe_title = sanitize_filename(title)
    out_path = output_dir / f"{safe_title}.txt"

    lines = [f"# {title}"]
    if metadata:
        if metadata.get("url"):
            lines.append(f"# 영상주소: {metadata['url']}")
        if metadata.get("channel"):
            channel_line = f"# 채널명: {metadata['channel']}"
            if metadata.get("channel_url"):
                channel_line += f" ({metadata['channel_url']})"
            lines.append(channel_line)
    lines.append(f"# language: {language}")
    lines.append("")
    lines.append("")
    header = "\n".join(lines)

    out_path.write_text(header + transcript, encoding="utf-8")
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube URL 또는 로컬 비디오/오디오 파일을 텍스트 스크립트로 변환합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        help="YouTube URL 또는 로컬 파일 경로 (mp4, mkv, mp3 등)",
    )
    parser.add_argument(
        "--output-dir", default="./transcripts",
        help="결과 저장 디렉토리 (기본: ./transcripts)",
    )
    parser.add_argument(
        "--model", default="large-v3",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper 모델 크기 (기본: large-v3)",
    )
    parser.add_argument(
        "--language", default=None,
        help="언어 코드 (예: ko, en). 미지정 시 자동 감지",
    )
    parser.add_argument(
        "--device", default="auto", choices=["auto", "cpu", "cuda"],
        help="추론 장치 (기본: auto)",
    )
    parser.add_argument(
        "--keep-audio", action="store_true",
        help="변환 후 다운로드/추출한 mp3 파일을 유지 (원본 파일은 항상 보존)",
    )
    parser.add_argument(
        "--extract-video", action="store_true",
        help="로컬 비디오를 mp3로 사전 추출 후 변환 (기본은 비디오 직접 전달, 더 빠름)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()

    audio_path: Path | None = None
    is_temp = False

    try:
        audio_path, title, is_temp, metadata = prepare_audio(
            args.input, output_dir, extract_video=args.extract_video,
        )
        print(f"[READY] 미디어 준비 완료: {audio_path}")
        if metadata.get("channel"):
            print(f"[META] 채널: {metadata['channel']}")
        if metadata.get("url"):
            print(f"[META] 영상주소: {metadata['url']}")

        transcript, language = transcribe_audio(
            audio_path,
            model_size=args.model,
            language=args.language,
            device=args.device,
        )

        out_path = save_transcript(output_dir, title, transcript, language, metadata)
        print(f"[SAVE] 스크립트 저장: {out_path}")

        if is_temp and not args.keep_audio:
            try:
                audio_path.unlink()
                print(f"[CLEAN] 임시 오디오 삭제: {audio_path.name}")
            except OSError as e:
                print(f"[CLEAN] 삭제 실패 (무시): {e}")

        return 0

    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
