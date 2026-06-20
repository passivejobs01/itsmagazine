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

## 설치

> 이 저장소 도구들은 **홈 공용 venv 하나(`~/.venv`)** 를 함께 씁니다.
> 저장소 **루트의 `setup_cuda_env.sh`** 한 번이면 `~/.venv` 생성 + CUDA PyTorch + 각 도구
> requirements(이 폴더 포함)까지 설치됩니다. **도구마다 venv를 새로 만들지 마세요.**

### 1) Python + 시스템 의존성
Python **3.10+** 필요. (STT 점검엔 ffmpeg 권장)
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg
```

### 2) 공용 venv 셋업 (저장소 루트에서, 한 번만)
```bash
cd ..                  # 저장소 루트로 (setup_cuda_env.sh 위치)
bash setup_cuda_env.sh
source ~/.venv/bin/activate
```
> 이미 `local-ai-usecase` 셋업 때 `~/.venv` 를 만들었다면 **다시 만들 필요 없이** 활성화만 하면 됩니다:
> `source ~/.venv/bin/activate`
>
> 점검 스크립트만 가볍게 돌리려면 수동 설치도 가능:
> `python3 -m venv ~/.venv && source ~/.venv/bin/activate`
> → `pip install torch --index-url https://download.pytorch.org/whl/cu126`
> → `pip install -r ubuntu-homelab/requirements.txt`
> (`check_ollama.py` 만 쓸 거면 표준 라이브러리만 필요 → 설치 없이 실행 가능)

### 3) (선택) Ollama
`check_ollama.py` 로 점검하려면 Ollama 서버와 모델이 있어야 합니다.
```bash
curl -fsSL https://ollama.com/install.sh | sh   # 설치(서버는 보통 자동 실행)
ollama pull gemma4:12b                           # 점검에 쓸 모델
```

## 사용법

```bash
source ~/.venv/bin/activate        # 공용 venv 활성화

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

## 참고

- 접속 주소는 코드에 하드코딩하지 않았습니다. Ollama는 기본 `http://localhost:11434`,
  필요 시 `--host` 옵션이나 `OLLAMA_HOST` 환경변수로 바꿉니다.
- faster-whisper 는 CTranslate2 기반이며, GPU 사용 시 cuDNN 이 필요합니다
  (보통 PyTorch CUDA 빌드를 설치하면 함께 해결됩니다).

## 환경

- Ubuntu (NVIDIA GPU)
- Python 3.10+
- 의존성은 `requirements.txt` 참고 (Ollama 점검은 표준 라이브러리만 사용)
