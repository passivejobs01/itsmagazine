"""
Content Tools API 테스트 스크립트
사용법: python test_api.py [--base-url URL]
"""

import argparse
import json
import sys
import time

import httpx

DEFAULT_BASE_URL = "http://localhost:8003"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def print_result(label: str, data, indent: int = 2):
    prefix = " " * indent
    if isinstance(data, dict):
        print(f"{prefix}{label}:")
        for k, v in data.items():
            val = str(v)[:120] + "..." if len(str(v)) > 120 else v
            print(f"{prefix}  {k}: {val}")
    elif isinstance(data, list):
        print(f"{prefix}{label}: ({len(data)}개)")
        for i, item in enumerate(data[:3]):
            print(f"{prefix}  [{i+1}] {item}")
        if len(data) > 3:
            print(f"{prefix}  ... 외 {len(data)-3}개")
    else:
        print(f"{prefix}{label}: {data}")


def test_health(client: httpx.Client, base_url: str):
    print_section("헬스 체크  GET /")
    resp = client.get(f"{base_url}/")
    resp.raise_for_status()
    print_result("응답", resp.json())
    print("  ✅ 정상")


def test_channel_videos(client: httpx.Client, base_url: str):
    print_section("채널 영상 목록  GET /youtube/channel-videos")
    url = "https://www.youtube.com/@your-channel"
    limit = 3
    print(f"  채널: {url}  limit={limit}")

    t0 = time.time()
    resp = client.get(
        f"{base_url}/youtube/channel-videos",
        params={"channel_url": url, "limit": limit},
        timeout=120,
    )
    elapsed = time.time() - t0

    if resp.status_code != 200:
        print(f"  ❌ 오류 {resp.status_code}: {resp.text[:200]}")
        return

    data = resp.json()
    print(f"  채널명: {data['channel_name']}  총 {data['total']}개  ({elapsed:.1f}초)")
    for i, v in enumerate(data["videos"][:3], 1):
        print(f"  [{i}] {v['title'][:60]}  |  {v['upload_date']}  |  조회수 {v['view_count']}")
    print("  ✅ 정상")


def test_my_channel(client: httpx.Client, base_url: str):
    print_section("내 채널  GET /youtube/my-channel")
    print("  limit=3")

    t0 = time.time()
    resp = client.get(f"{base_url}/youtube/my-channel", params={"limit": 3}, timeout=120)
    elapsed = time.time() - t0

    if resp.status_code != 200:
        print(f"  ❌ 오류 {resp.status_code}: {resp.text[:200]}")
        return

    data = resp.json()
    print(f"  채널명: {data['channel_name']}  총 {data['total']}개  ({elapsed:.1f}초)")
    for i, v in enumerate(data["videos"][:3], 1):
        print(f"  [{i}] {v['title'][:60]}")
    print("  ✅ 정상")


def test_recent_videos(client: httpx.Client, base_url: str):
    print_section("최근 영상 수집  GET /research/recent-videos  (YouTube Data API)")
    channel_urls = ",".join([
        "https://www.youtube.com/@CraftComputing",
        "https://www.youtube.com/@TechHut",
    ])
    params = {
        "channel_urls": channel_urls,
        "days": 14,
        "limit_per_channel": 3,
    }
    print(f"  채널: CraftComputing, TechHut  최근 {params['days']}일")

    t0 = time.time()
    resp = client.get(f"{base_url}/research/recent-videos", params=params, timeout=60)
    elapsed = time.time() - t0

    if resp.status_code != 200:
        print(f"  ❌ 오류 {resp.status_code}: {resp.text[:300]}")
        return

    data = resp.json()
    print(f"  수집 시각: {data['collected_at']}  총 {data['total']}개  ({elapsed:.1f}초)")
    for i, v in enumerate(data["videos"][:5], 1):
        print(f"  [{i}] [{v['channel_name']}] {v['title'][:55]}  |  {v['upload_date']}  |  조회수 {v['view_count']}")
    print("  ✅ 정상")


def test_transcribe(client: httpx.Client, base_url: str, video_url: str, model: str = "small"):
    print_section(f"STT 변환  POST /transcribe  (model={model})")
    print(f"  URL: {video_url}")
    print(f"  ⚠️  시간이 걸릴 수 있습니다 (모델 로딩 + 다운로드 + 변환)")

    payload = {
        "url": video_url,
        "model": model,
        "language": None,
        "keep_audio": False,
    }

    t0 = time.time()
    resp = client.post(f"{base_url}/transcribe", json=payload, timeout=600)
    elapsed = time.time() - t0

    if resp.status_code != 200:
        print(f"  ❌ 오류 {resp.status_code}: {resp.text[:300]}")
        return

    data = resp.json()
    preview = data["transcript"][:200].replace("\n", " ")
    print(f"  제목: {data['title'][:70]}")
    print(f"  채널: {data['channel']}")
    print(f"  언어: {data['language']}")
    print(f"  저장: {data['saved_path']}")
    print(f"  내용 미리보기: {preview}...")
    print(f"  소요 시간: {elapsed:.1f}초")
    print("  ✅ 정상")


def run_all(base_url: str, transcribe_url: str | None, transcribe_model: str):
    print(f"\n🔍 테스트 대상: {base_url}")

    with httpx.Client() as client:
        try:
            test_health(client, base_url)
        except Exception as e:
            print(f"  ❌ 헬스 체크 실패: {e}")
            print("  서버가 실행 중인지 확인하세요.")
            return

        for test_fn in [test_channel_videos, test_my_channel, test_recent_videos]:
            try:
                test_fn(client, base_url)
            except Exception as e:
                print(f"  ❌ 오류: {type(e).__name__}: {e}")

        if transcribe_url:
            try:
                test_transcribe(client, base_url, transcribe_url, transcribe_model)
            except Exception as e:
                print(f"  ❌ 오류: {type(e).__name__}: {e}")
        else:
            print_section("STT 변환  POST /transcribe")
            print("  ⏭️  건너뜀 (--transcribe-url 옵션으로 YouTube URL을 전달하면 테스트합니다)")

    print(f"\n{'='*60}")
    print("  테스트 완료")
    print('='*60)


def main():
    parser = argparse.ArgumentParser(description="Content Tools API 테스트")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"API 서버 기본 URL (기본: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--transcribe-url", default=None,
        help="STT 테스트에 사용할 YouTube URL (미지정 시 STT 테스트 건너뜀)",
    )
    parser.add_argument(
        "--transcribe-model", default="small",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="STT 테스트 시 사용할 Whisper 모델 (기본: small)",
    )
    args = parser.parse_args()

    run_all(
        base_url=args.base_url.rstrip("/"),
        transcribe_url=args.transcribe_url,
        transcribe_model=args.transcribe_model,
    )


if __name__ == "__main__":
    main()
