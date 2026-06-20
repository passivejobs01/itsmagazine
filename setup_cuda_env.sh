#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 홈서버 공용 CUDA Python 환경 셋업 (Linux / NVIDIA GPU, 예: RTX 3060)
#   - 홈 폴더에 공용 venv 생성 (~/.venv) → 모든 도구가 이 하나를 공유
#   - PyTorch CUDA 12.6 빌드 (드라이버 595 / CUDA 13과 하위호환)
#   - 저장소 안 각 도구(local-ai-usecase, ubuntu-homelab 등)의
#     requirements.txt 를 같은 venv 에 설치
#
# 실행(저장소 루트에서):  bash setup_cuda_env.sh
# venv 경로 바꾸려면:      VENV=~/myenv bash setup_cuda_env.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"     # 저장소 루트(이 스크립트 위치)
VENV="${VENV:-$HOME/.venv}"                # 공용 venv 경로 (기본 ~/.venv)

echo "==> Python 버전 확인"
python3 --version

echo "==> 공용 venv 생성: $VENV"
python3 -m venv "$VENV"
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel

echo "==> PyTorch (CUDA 12.6 빌드) 설치"
# 먼저 cu126 휠로 설치 → 이후 requirements 의 무버전 torch 는 '이미 충족'으로 건너뜀(CUDA 빌드 유지)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126

echo "==> 각 도구의 requirements.txt 설치 (있으면)"
for d in local-ai-usecase ubuntu-homelab; do
  if [ -f "$HERE/$d/requirements.txt" ]; then
    echo "   - $d/requirements.txt"
    pip install -r "$HERE/$d/requirements.txt"
  fi
done

echo "==> TTS/오디오 보강 의존성"
pip install -U qwen-tts soundfile python-dotenv numpy

echo "==> CUDA 인식 검증"
python - <<'PY'
import torch
ok = torch.cuda.is_available()
print("torch:", torch.__version__)
print("CUDA available:", ok)
if ok:
    print("device:", torch.cuda.get_device_name(0))
    print("bf16 supported:", torch.cuda.is_bf16_supported())
PY

echo
echo "완료. 앞으로는 이 공용 venv 하나만 활성화해서 모든 도구 실행:"
echo "  source $VENV/bin/activate"
echo
echo "ffmpeg(오디오 처리용)이 없으면:  sudo apt install -y ffmpeg"
