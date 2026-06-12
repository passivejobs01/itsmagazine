#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# voice_clone.py 용 CUDA Python 환경 셋업 (Linux VM / RTX 3060)
#   - Python venv 생성 (.venv)
#   - PyTorch CUDA 12.6 빌드 (드라이버 595/CUDA13과 하위호환)
#   - Qwen3-TTS + 오디오/STT 의존성
# 실행:  bash setup_cuda_env.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Python 버전 확인"
python3 --version

echo "==> venv 생성 (.venv)"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel

echo "==> PyTorch (CUDA 12.6 빌드) 설치"
# 드라이버가 CUDA 13.2를 지원하므로 cu126 휠은 정상 동작 (하위호환)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126

echo "==> Qwen3-TTS + 오디오/STT 의존성 설치"
pip install -U qwen-tts soundfile faster-whisper python-dotenv numpy

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
echo "완료. 이후 사용 전 항상 가상환경 활성화:"
echo "  source $(pwd)/.venv/bin/activate"
echo
echo "ffmpeg(mp3 저장용)이 없으면:  sudo apt install -y ffmpeg"
