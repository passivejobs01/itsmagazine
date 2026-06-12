"""
Ollama 서버 연결 및 요약 테스트
사용법: python test_ollama.py
"""

import json
import os
import urllib.request
import urllib.error

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

SAMPLE_SUBTITLE = """
In this video, I'm going to show you how to switch your entire family from Windows to Linux.
It's been a long journey, but with Steam and Proton, gaming on Linux is finally ready for primetime.
We have six people in our house, and five of us play PC games.
I started with my own machine, then moved on to my kids' PCs.
The biggest challenge was getting everyone comfortable with the new interface.
I chose Ubuntu as the base OS because of its large community and hardware support.
Most of our games work perfectly through Steam's Proton compatibility layer.
Only a couple of older titles had issues, and we found workarounds for most of them.
The kids actually adapted faster than I expected.
Overall, I'm very happy with the switch and don't miss Windows at all.
"""


def check_server():
    print(f"[1] 서버 연결 확인: {OLLAMA_HOST}")
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
        models = [m["name"] for m in data.get("models", [])]
        print(f"    ✅ 연결 성공")
        print(f"    사용 가능한 모델: {models}")
        return models
    except urllib.error.URLError as e:
        print(f"    ❌ 연결 실패: {e}")
        return []


def check_model(models):
    print(f"\n[2] 모델 확인: {MODEL}")
    matched = [m for m in models if MODEL in m]
    if matched:
        print(f"    ✅ 모델 있음: {matched}")
        return True
    else:
        print(f"    ❌ 모델 없음. 설치된 모델: {models}")
        return False


def test_summarize():
    print(f"\n[3] 요약 테스트 (모델: {MODEL})")
    print(f"    입력 자막 길이: {len(SAMPLE_SUBTITLE)}자")

    prompt = f"""다음은 유튜브 영상의 자막입니다. 핵심 내용을 한국어로 3~5문장으로 요약해주세요.

자막:
{SAMPLE_SUBTITLE.strip()}

요약:"""

    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.load(r)

        summary = result.get("response", "").strip()
        elapsed = result.get("total_duration", 0) / 1e9  # 나노초 → 초

        print(f"    ✅ 요약 완료 ({elapsed:.1f}초)")
        print(f"\n--- 요약 결과 ---")
        print(summary)
        print("-----------------")

    except urllib.error.URLError as e:
        print(f"    ❌ 요약 실패: {e}")
    except Exception as e:
        print(f"    ❌ 오류: {type(e).__name__}: {e}")


def main():
    print(f"=== Ollama 서버 테스트 ===\n")
    models = check_server()
    if not models:
        return

    model_ok = check_model(models)
    if not model_ok:
        print(f"\n    모델을 먼저 설치하세요: ollama pull {MODEL}")
        return

    test_summarize()
    print(f"\n=== 테스트 완료 ===")


if __name__ == "__main__":
    main()
