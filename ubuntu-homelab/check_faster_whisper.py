#!/usr/bin/env python3
"""
check_faster_whisper.py — faster-whisper(STT)가 잘 설치됐는지 확인하는 샘플.

faster-whisper 는 CTranslate2 기반이라, 단순히 import 가 되는 것뿐 아니라
GPU(CUDA + cuDNN)에서 모델 로딩까지 되는지 확인하는 게 중요합니다.

사용법:
    # 1) 설치/로딩만 확인 (가장 가벼운 tiny 모델을 받아 로딩)
    python check_faster_whisper.py

    # 2) 실제 음성 파일로 받아쓰기까지 테스트
    python check_faster_whisper.py --audio sample.wav --model small --language ko

옵션:
    --model     모델 크기 (tiny, base, small, medium, large-v3, large-v3-turbo ...) 기본 tiny
    --device    cuda / cpu / auto  (기본 auto: GPU 있으면 cuda, 없으면 cpu)
    --audio     받아쓰기 테스트할 오디오 파일 경로 (생략 시 로딩까지만 확인)
    --language  언어 코드 (예: ko, en). 생략 시 자동 감지

설치:
    pip install faster-whisper
"""

import argparse
import sys
import time


def pick_device(requested: str) -> tuple[str, str]:
    """device(cuda/cpu)와 compute_type을 결정한다."""
    if requested == "cpu":
        return "cpu", "int8"
    if requested == "cuda":
        return "cuda", "float16"

    # auto: torch가 있으면 GPU 여부로 판단, 없으면 cuda를 먼저 시도
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
        return "cpu", "int8"
    except ImportError:
        return "cuda", "float16"  # 일단 GPU 시도 → 실패 시 아래에서 CPU로 폴백


def main() -> int:
    ap = argparse.ArgumentParser(description="faster-whisper 설치/동작 확인")
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--audio", default=None)
    ap.add_argument("--language", default=None)
    args = ap.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("❌ faster-whisper 가 설치되어 있지 않습니다.")
        print("   → pip install faster-whisper")
        return 1

    print(f"✅ faster-whisper import 성공")

    device, compute_type = pick_device(args.device)
    print(f"• 모델       : {args.model}")
    print(f"• device     : {device} ({compute_type})")
    print("• 모델 로딩 중... (최초 1회는 다운로드로 시간이 걸립니다)")

    t0 = time.time()
    try:
        model = WhisperModel(args.model, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"❌ GPU 모델 로딩 실패: {e}")
        if device == "cuda":
            print("• CPU로 다시 시도합니다...")
            try:
                device, compute_type = "cpu", "int8"
                model = WhisperModel(args.model, device=device, compute_type=compute_type)
            except Exception as e2:
                print(f"❌ CPU 로딩도 실패: {e2}")
                return 1
        else:
            return 1

    print(f"✅ 모델 로딩 성공 ({time.time() - t0:.1f}s) — device={device}")

    if not args.audio:
        print("\n👉 설치/로딩 확인 완료. 실제 받아쓰기는 --audio 옵션으로 테스트하세요.")
        return 0

    # 실제 받아쓰기 테스트
    print(f"\n• 받아쓰기 테스트: {args.audio}")
    t1 = time.time()
    try:
        segments, info = model.transcribe(args.audio, language=args.language)
        print(f"• 감지 언어: {info.language} (확률 {info.language_probability:.2f})")
        print("─" * 50)
        for seg in segments:
            print(f"[{seg.start:6.2f}s → {seg.end:6.2f}s] {seg.text.strip()}")
        print("─" * 50)
        print(f"✅ 받아쓰기 완료 ({time.time() - t1:.1f}s)")
    except FileNotFoundError:
        print(f"❌ 오디오 파일을 찾을 수 없습니다: {args.audio}")
        return 1
    except Exception as e:
        print(f"❌ 받아쓰기 중 오류: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
