# Ubuntu Local AI 홈서버 — 설치 점검 샘플

Ubuntu 홈서버에 **NVIDIA 드라이버 + CUDA + Local AI 스택**을 설치한 뒤,
"제대로 깔렸는지"를 하나씩 확인하는 가장 작은 점검 스크립트 모음입니다.
새 서버를 셋업하고 **순서대로** 돌려보면 어디서 막혔는지 빠르게 알 수 있습니다.

## 점검 순서

| 순서 | 스크립트 | 확인 내용 |
|---|---|---|
| 1 | `check_cuda.py` | NVIDIA GPU / CUDA가 PyTorch에서 인식되는가 (VRAM·Compute capability·실연산) |
| 2 | `check_faster_whisper.py` | STT 엔진(faster-whisper)이 설치·로딩되는가 (GPU/CPU, 실제 받아쓰기) |
| 3 | `check_ollama.py` | 로컬 LLM 서버(Ollama)에 접속되고 모델 생성이 되는가 |

## 사용법

```bash
# 0) (권장) 가상환경
python3 -m venv .venv && source .venv/bin/activate

# 1) CUDA 인식 확인
python check_cuda.py

# 2) faster-whisper 확인 (로딩만 → 실제 음성까지)
python check_faster_whisper.py
python check_faster_whisper.py --audio sample.wav --model small --language ko

# 3) Ollama 접속 확인 (목록만 → 실제 생성까지)
python check_ollama.py
python check_ollama.py --model gemma4:12b --prompt "한 문장으로 자기소개 해줘"
```

각 스크립트는 성공 시 종료코드 `0`, 실패 시 `1`을 반환하므로 셸에서 조건 분기로도 쓸 수 있습니다.

## 설치 메모

- **PyTorch(CUDA 빌드)**: CPU 빌드가 깔리면 GPU가 안 잡힙니다. CUDA 빌드로 설치하세요.
  ```bash
  pip install torch --index-url https://download.pytorch.org/whl/cu126
  ```
- **faster-whisper**: `pip install faster-whisper` (CTranslate2가 함께 설치됨, GPU엔 cuDNN 필요)
- **Ollama**: `curl -fsSL https://ollama.com/install.sh | sh` → `ollama pull <모델>`
- 접속 주소는 코드에 하드코딩하지 않았습니다. Ollama는 기본 `http://localhost:11434`,
  필요 시 `--host` 옵션이나 `OLLAMA_HOST` 환경변수로 바꿉니다.

## 환경

- Ubuntu (NVIDIA GPU)
- Python 3.10+
- 의존성은 `requirements.txt` 참고 (Ollama 점검은 표준 라이브러리만 사용)
