#!/usr/bin/env python3
"""
check_cuda.py — NVIDIA GPU / CUDA가 PyTorch에서 잘 인식되는지 확인하는 샘플.

홈서버에 NVIDIA 드라이버 + CUDA + PyTorch(cu1xx 빌드)를 설치한 뒤,
"진짜로 GPU가 잡혔는지"를 가장 먼저 확인할 때 쓰는 코드입니다.

사용법:
    python check_cuda.py

기대 결과:
    - CUDA available: True
    - GPU 이름 / VRAM / Compute capability 가 출력되면 정상.

GPU가 안 잡힐 때 점검 순서:
    1) `nvidia-smi` 가 동작하는가(드라이버 설치 여부)
    2) torch가 CPU 빌드로 깔리지 않았는가
       → `pip install torch --index-url https://download.pytorch.org/whl/cu126` 처럼 CUDA 빌드 설치
    3) 드라이버 버전이 torch가 요구하는 CUDA 런타임보다 낮지 않은가
"""

import sys


def main() -> int:
    try:
        import torch
    except ImportError:
        print("❌ torch 가 설치되어 있지 않습니다.")
        print("   → pip install torch --index-url https://download.pytorch.org/whl/cu126")
        return 1

    print(f"• torch 버전        : {torch.__version__}")
    print(f"• torch가 빌드된 CUDA: {torch.version.cuda or '없음(CPU 빌드)'}")

    available = torch.cuda.is_available()
    print(f"• CUDA available    : {available}")

    if not available:
        print("\n❌ GPU(CUDA)가 인식되지 않았습니다. CPU로만 동작합니다.")
        print("   확인: `nvidia-smi` 가 되는지 / torch가 CPU 빌드는 아닌지 점검하세요.")
        return 1

    # cuDNN (faster-whisper 등 일부 라이브러리가 사용)
    try:
        cudnn = torch.backends.cudnn.version()
    except Exception:
        cudnn = None
    print(f"• cuDNN 버전        : {cudnn if cudnn else '확인 불가'}")

    count = torch.cuda.device_count()
    print(f"• 인식된 GPU 수     : {count}")

    for i in range(count):
        name = torch.cuda.get_device_name(i)
        major, minor = torch.cuda.get_device_capability(i)
        total_gb = torch.cuda.get_device_properties(i).total_memory / (1024 ** 3)
        print(f"\n  [GPU {i}] {name}")
        print(f"     - VRAM              : {total_gb:.1f} GB")
        print(f"     - Compute capability: {major}.{minor}")

    # 실제로 GPU에 텐서를 올려 연산이 되는지 최종 확인
    try:
        x = torch.rand(1024, 1024, device="cuda")
        y = (x @ x).sum().item()
        print(f"\n✅ GPU 연산 테스트 통과 (sample sum={y:.1f}) — 사용 준비 완료!")
    except Exception as e:
        print(f"\n❌ GPU 연산 중 오류: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
