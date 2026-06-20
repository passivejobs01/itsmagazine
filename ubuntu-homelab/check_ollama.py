#!/usr/bin/env python3
"""
check_ollama.py — 로컬 Ollama 서버 접속/동작을 확인하는 샘플.

외부 의존성 없이 표준 라이브러리(urllib)만으로 동작합니다.

사용법:
    # 1) 서버 접속 + 설치된 모델 목록 확인
    python check_ollama.py

    # 2) 특정 모델로 실제 한 줄 생성까지 테스트
    python check_ollama.py --model gemma4:12b --prompt "한 문장으로 자기소개 해줘"

접속 주소 지정(우선순위: --host 옵션 > OLLAMA_HOST 환경변수 > 기본값):
    기본값: http://localhost:11434
    예) OLLAMA_HOST=http://localhost:11434 python check_ollama.py

설치/실행(서버 쪽):
    curl -fsSL https://ollama.com/install.sh | sh   # 설치
    ollama serve                                    # 서버 실행(보통 자동 실행)
    ollama pull gemma4:12b                           # 모델 받기
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def http_get(url: str, timeout: int = 5):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, payload: dict, timeout: int = 120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ollama 서버 접속 확인")
    ap.add_argument("--host", default=None,
                    help="Ollama 주소 (기본: OLLAMA_HOST 또는 http://localhost:11434)")
    ap.add_argument("--model", default=None, help="생성 테스트할 모델 이름")
    ap.add_argument("--prompt", default="안녕하세요. 한 문장으로 인사해 주세요.")
    args = ap.parse_args()

    host = (args.host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
    print(f"• Ollama 주소: {host}")

    # 1) 접속 + 모델 목록 (/api/tags)
    try:
        tags = http_get(f"{host}/api/tags")
    except urllib.error.URLError as e:
        print(f"❌ 서버에 접속할 수 없습니다: {e.reason}")
        print("   확인: `ollama serve` 가 실행 중인지 / 주소·포트가 맞는지 점검하세요.")
        return 1
    except Exception as e:
        print(f"❌ 접속 중 오류: {e}")
        return 1

    models = [m.get("name", "?") for m in tags.get("models", [])]
    print(f"✅ 서버 접속 성공 — 설치된 모델 {len(models)}개")
    for name in models:
        print(f"     - {name}")

    if not models:
        print("\n⚠️ 설치된 모델이 없습니다. 예) ollama pull gemma4:12b")

    # 2) 실제 생성 테스트 (--model 지정 시)
    if args.model:
        print(f"\n• 생성 테스트: model={args.model}")
        try:
            result = http_post(f"{host}/api/generate", {
                "model": args.model,
                "prompt": args.prompt,
                "stream": False,
            })
            print("─" * 50)
            print(result.get("response", "").strip())
            print("─" * 50)
            print("✅ 생성 테스트 성공 — Ollama가 정상 동작합니다!")
        except urllib.error.HTTPError as e:
            print(f"❌ 생성 실패(HTTP {e.code}). 모델 이름이 맞는지 확인하세요(ollama list).")
            return 1
        except Exception as e:
            print(f"❌ 생성 중 오류: {e}")
            return 1
    else:
        print("\n👉 접속/목록 확인 완료. 실제 생성은 --model 옵션으로 테스트하세요.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
