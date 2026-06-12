"""
YouTube 채널의 영상 목록을 수집하고 마크다운 파일로 저장하는 도구.

사용 예시:
    # 채널 URL로 수집
    python youtube_channel_list.py https://www.youtube.com/@your-channel

    # 저장 경로 지정
    python youtube_channel_list.py https://www.youtube.com/@your-channel --output-dir "./output"

    # 최근 N개만 수집
    python youtube_channel_list.py https://www.youtube.com/@your-channel --limit 20

    # CSV로도 저장
    python youtube_channel_list.py https://www.youtube.com/@your-channel --csv
"""

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path

import yt_dlp


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name.strip().strip(".")[:100] or "channel"


def format_duration(seconds: int | None) -> str:
    """초를 MM:SS 또는 HH:MM:SS 형식으로 변환."""
    if not seconds:
        return "-"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_date(date_str: str | None) -> str:
    """YYYYMMDD → YYYY-MM-DD 변환."""
    if not date_str or len(date_str) != 8:
        return "-"
    try:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    except Exception:
        return date_str


def format_views(views: int | None) -> str:
    """조회수를 읽기 쉬운 형식으로 변환."""
    if views is None:
        return "-"
    if views >= 10000:
        return f"{views // 10000}만"
    if views >= 1000:
        return f"{views / 1000:.1f}천"
    return str(views)


def fetch_channel_videos(channel_url: str, limit: int | None = None) -> tuple[list[dict], str]:
    """
    채널의 영상 메타데이터를 수집.

    Returns:
        (videos, channel_name)
        videos: 각 영상의 메타데이터 딕셔너리 리스트
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,       # 영상 다운로드 없이 메타데이터만
        "skip_download": True,
    }

    if limit:
        ydl_opts["playlistend"] = limit

    print(f"[FETCH] 채널 정보 수집 중: {channel_url}")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    if not info:
        raise ValueError("채널 정보를 가져오지 못했습니다.")

    channel_name = info.get("channel") or info.get("uploader") or info.get("title") or "Unknown"
    entries = info.get("entries") or []

    # 플레이리스트 안에 또 플레이리스트가 있는 경우 (채널 탭 구조) 평탄화
    flat_entries = []
    for entry in entries:
        if entry.get("_type") == "playlist":
            flat_entries.extend(entry.get("entries") or [])
        else:
            flat_entries.append(entry)

    videos = []
    for i, entry in enumerate(flat_entries, 1):
        if not entry:
            continue

        video_id = entry.get("id") or ""
        url = entry.get("url") or entry.get("webpage_url") or ""
        if video_id and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={video_id}"

        videos.append({
            "no": i,
            "title": entry.get("title") or "-",
            "url": url,
            "upload_date": format_date(entry.get("upload_date")),
            "duration": format_duration(entry.get("duration")),
            "views": format_views(entry.get("view_count")),
            "views_raw": entry.get("view_count"),
            "description": (entry.get("description") or "")[:200],
        })

    print(f"[DONE] {channel_name} — 총 {len(videos)}개 영상 수집 완료")
    return videos, channel_name


def save_markdown(videos: list[dict], channel_name: str, channel_url: str, output_dir: Path) -> Path:
    """영상 목록을 마크다운 파일로 저장."""
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(channel_name)
    now = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = output_dir / f"{safe_name}_영상목록_{now}.md"

    lines = [
        f"# {channel_name} 영상 목록",
        f"",
        f"- **채널 URL**: {channel_url}",
        f"- **수집일**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **총 영상 수**: {len(videos)}개",
        f"",
        f"---",
        f"",
        f"## 영상 목록",
        f"",
        f"| # | 제목 | 업로드일 | 재생시간 | 조회수 |",
        f"|---|------|---------|---------|-------|",
    ]

    for v in videos:
        title_link = f"[{v['title']}]({v['url']})" if v["url"] else v["title"]
        lines.append(
            f"| {v['no']} | {title_link} | {v['upload_date']} | {v['duration']} | {v['views']} |"
        )

    lines += [
        f"",
        f"---",
        f"",
        f"## 콘텐츠 분석 메모",
        f"",
        f"> 이 섹션에 기존 영상 패턴, 인기 주제, 파생 아이디어 등을 기록하세요.",
        f"",
        f"### 주요 패턴",
        f"- ",
        f"",
        f"### 파생 콘텐츠 아이디어",
        f"- ",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[SAVE] 마크다운 저장: {out_path}")
    return out_path


def save_csv(videos: list[dict], channel_name: str, output_dir: Path) -> Path:
    """영상 목록을 CSV 파일로 저장."""
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(channel_name)
    now = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = output_dir / f"{safe_name}_영상목록_{now}.csv"

    fieldnames = ["no", "title", "url", "upload_date", "duration", "views", "description"]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(videos)

    print(f"[SAVE] CSV 저장: {out_path}")
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube 채널의 영상 목록을 수집하고 마크다운/CSV로 저장합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "channel_url",
        help="YouTube 채널 URL (예: https://www.youtube.com/@your-channel)",
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="결과 저장 디렉토리 (기본: 현재 디렉토리)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="수집할 최대 영상 수 (기본: 전체)",
    )
    parser.add_argument(
        "--csv", action="store_true",
        help="마크다운 외에 CSV 파일도 함께 저장",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()

    try:
        videos, channel_name = fetch_channel_videos(args.channel_url, limit=args.limit)

        if not videos:
            print("[INFO] 수집된 영상이 없습니다.")
            return 0

        save_markdown(videos, channel_name, args.channel_url, output_dir)

        if args.csv:
            save_csv(videos, channel_name, output_dir)

        return 0

    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
