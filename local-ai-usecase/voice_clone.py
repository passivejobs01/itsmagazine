"""
Qwen3-TTS 음성 클로닝 파이프라인 (음성 프로필 등록·재사용 지원)

오픈소스 Qwen3-TTS(qwen-tts) 로컬 가중치로 참조 음성의 음색을 클로닝한다 (GPU 권장).

핵심 개념
─────────
Qwen3-TTS의 클로닝은 "제로샷" 방식이라 별도의 학습/파인튜닝 단계가 없다.
대신 참조 음성(ref_audio) + 참조 텍스트(ref_text)로부터 "prompt 특징"을 한 번 계산해
재사용할 수 있다. 이 스크립트는 그 특징과 메타데이터를 "음성 프로필"로 등록해두고,
이후에는 이름만으로 합성하도록 한다.

사용 흐름
─────────
1) 음성 등록 (한 번만):
    python voice_clone.py register --name 내목소리 --ref-audio sample.wav \
        --ref-text "안녕하세요 반갑습니다"
    # 참조 텍스트 생략 시 faster-whisper(video_pipeline)로 자동 추출:
    python voice_clone.py register --name 내목소리 --ref-audio sample.wav --auto-ref-text

2) 등록된 음성으로 합성 (참조 음성/텍스트 재입력 불필요):
    python voice_clone.py generate --voice 내목소리 \
        --text "오늘은 홈서버 구축 방법을 알려드리겠습니다." -o intro.wav

   ▸ --text-file (또는 --textfile): 합성할 대본을 .txt 파일에서 읽는다.
        python voice_clone.py generate --voice 내목소리 --text-file D:/대본/intro.txt
     - 결과 wav는 --output을 주지 않으면 "텍스트 파일이 있는 폴더"에 저장된다.
     - 파일명 형식: {voice}_{텍스트파일이름}_MMDD_HHMMSS.wav
       예) D:/대본/intro.txt → D:/대본/내목소리_intro_0604_142233.wav
     - --output 으로 경로/파일명을 직접 지정할 수도 있다 (확장자 .mp3/.wav 모두 가능).

3) 등록 목록 확인 / 삭제:
    python voice_clone.py list
    python voice_clone.py remove --name 내목소리

즉석 모드 (등록 없이 1회용):
    python voice_clone.py generate --ref-audio sample.wav --auto-ref-text \
        --text "테스트 문장입니다."

설치:
    pip install -U qwen-tts soundfile   # mp3 저장에는 ffmpeg도 필요

참고:
    - 참조 음성은 3~15초의 깨끗한(잡음 없는) 발화가 가장 좋다 (10~15초 권장).
    - 지원 언어: Chinese, English, German, Italian, Portuguese, Spanish,
      Japanese, Korean, French, Russian
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import os

load_dotenv()


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
# Base 모델 = 제로샷 음성 클로닝용 (CustomVoice 모델은 사전 정의 화자 전용이라 클로닝 불가)
QWEN_TTS_MODEL = os.getenv("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")

# 결과 wav 저장 기본 폴더
TTS_OUTPUT_DIR = os.getenv("TTS_OUTPUT_DIR", str(Path(__file__).parent / "tts_output"))

# 등록된 음성 프로필 저장 폴더 (이름.json + 이름.ref.<ext> + 이름.prompt.pt)
VOICES_DIR = Path(os.getenv("TTS_VOICES_DIR", str(Path(__file__).parent / "voices")))

SUPPORTED_LANGUAGES = [
    "Chinese", "English", "German", "Italian", "Portuguese",
    "Spanish", "Japanese", "Korean", "French", "Russian",
]

# 언어 이름 → faster-whisper 코드 (참조 텍스트 자동 추출용)
_WHISPER_LANG = {
    "Korean": "ko", "English": "en", "Japanese": "ja", "Chinese": "zh",
    "German": "de", "French": "fr", "Spanish": "es", "Italian": "it",
    "Portuguese": "pt", "Russian": "ru",
}

_LOCAL_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


# ──────────────────────────────────────────────
# 디바이스 / 모델 로딩
# ──────────────────────────────────────────────
def detect_device() -> tuple[str, "object"]:
    """CUDA 가용 여부에 따라 (device_map, dtype)을 반환한다."""
    import torch
    if torch.cuda.is_available():
        return "cuda:0", torch.bfloat16
    return "cpu", torch.float32


def resolve_attn(attn: str, device_map: str) -> str:
    """attn 구현 선택. 'auto' → CUDA=sdpa, CPU=eager (flash_attention_2는 별도 설치 필요)."""
    if attn != "auto":
        return attn
    return "sdpa" if device_map.startswith("cuda") else "eager"


_MODEL_CACHE: dict = {}


def load_model(model_name: str, device: str = "auto", attn: str = "auto"):
    """Qwen3-TTS 모델을 로드한다 (프로세스 내 캐시). 반환: (model, device_map)."""
    try:
        import torch  # noqa: F401
        from qwen_tts import Qwen3TTSModel
    except ImportError as e:
        raise RuntimeError(
            "qwen-tts(또는 torch)가 설치되지 않았습니다.\n"
            "  pip install -U qwen-tts soundfile\n"
            f"원본 오류: {type(e).__name__}: {e}"
        )

    if device == "auto":
        device_map, dtype = detect_device()
    else:
        import torch
        device_map = device
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32

    attn_impl = resolve_attn(attn, device_map)
    cache_key = (model_name, device_map, str(dtype), attn_impl)
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached, device_map

    print(f"[TTS] 모델 로딩: {model_name}  device={device_map}  attn={attn_impl}")
    model = Qwen3TTSModel.from_pretrained(
        model_name,
        device_map=device_map,
        dtype=dtype,
        attn_implementation=attn_impl,
    )
    _MODEL_CACHE[cache_key] = model
    print("[TTS] 모델 로딩 완료")
    return model, device_map


def free_tts_models() -> None:
    """캐시된 Qwen3-TTS 모델을 해제해 GPU VRAM을 반환한다.

    단일 GPU를 STT(whisper)·요약(LLM)과 공유할 때, TTS를 쓰지 않는 동안 VRAM을
    비워 다른 작업이 GPU에 온전히 올라가도록 한다(부분 CPU 오프로드 방지).
    """
    import gc
    if not _MODEL_CACHE:
        return
    _MODEL_CACHE.clear()
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ──────────────────────────────────────────────
# 참조 텍스트 자동 추출 (faster-whisper 재사용)
# ──────────────────────────────────────────────
def auto_transcribe_ref(ref_audio: str, language: str | None = None) -> str:
    """참조 음성을 faster-whisper(video_pipeline.transcribe)로 STT해 참조 텍스트를 만든다.
    로컬 오디오 파일에만 적용 가능 (URL/base64는 미지원)."""
    src = Path(ref_audio)
    if not src.exists() or src.suffix.lower() not in _LOCAL_AUDIO_EXTS:
        raise ValueError(
            "--auto-ref-text는 로컬 오디오 파일에만 사용할 수 있습니다 "
            f"(현재: {ref_audio}). --ref-text로 직접 전달하세요."
        )
    try:
        from video_pipeline import transcribe
    except ImportError as e:
        raise RuntimeError(f"video_pipeline.transcribe를 불러올 수 없습니다: {e}")

    whisper_lang = _WHISPER_LANG.get(language) if language else None
    print(f"[TTS] 참조 텍스트 자동 추출(STT): {src.name}")
    text, detected = transcribe(src, model_size="small", language=whisper_lang)
    ref_text = " ".join(text.split())  # 줄바꿈 → 공백 정리
    if not ref_text.strip():
        raise RuntimeError("참조 음성에서 텍스트를 추출하지 못했습니다.")
    print(f"[TTS] 참조 텍스트({detected}): {ref_text[:80]}{'...' if len(ref_text) > 80 else ''}")
    return ref_text


def resolve_ref_text(ref_text, ref_text_file, auto_ref_text, ref_audio, language) -> str:
    """참조 텍스트를 직접/파일/자동 STT 중 주어진 방식으로 확보한다."""
    if auto_ref_text:
        return auto_transcribe_ref(ref_audio, language)
    if ref_text is not None:
        return ref_text
    if ref_text_file:
        content = Path(ref_text_file).read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"참조 텍스트 파일이 비어 있습니다: {ref_text_file}")
        return content
    raise ValueError("참조 텍스트가 필요합니다 (--ref-text / --ref-text-file / --auto-ref-text).")


# ──────────────────────────────────────────────
# 음성 프로필 (등록 / 로드 / 목록)
# ──────────────────────────────────────────────
def _profile_path(name: str) -> Path:
    return VOICES_DIR / f"{name}.json"


def register_voice(name: str, ref_audio: str, ref_text: str) -> dict:
    """음성 프로필을 등록한다. 로컬 오디오는 voices 폴더로 복사해 자체 보관한다."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)

    src = Path(ref_audio)
    if src.exists() and src.suffix.lower() in _LOCAL_AUDIO_EXTS:
        # 프로필이 원본 위치에 의존하지 않도록 voices 폴더로 복사
        dest = VOICES_DIR / f"{name}.ref{src.suffix.lower()}"
        shutil.copy2(src, dest)
        stored_audio = str(dest)
    else:
        # URL / base64 문자열은 그대로 보관
        stored_audio = ref_audio

    profile = {
        "name": name,
        "ref_audio": stored_audio,
        "ref_text": ref_text,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _profile_path(name).write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 기존 prompt 캐시가 있으면 무효화 (참조가 바뀌었을 수 있음)
    cache = VOICES_DIR / f"{name}.prompt.pt"
    if cache.exists():
        cache.unlink()
    return profile


def load_voice(name: str) -> dict:
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"등록된 음성이 없습니다: '{name}'. 먼저 register로 등록하세요.\n"
            f"  (등록 목록: python voice_clone.py list)"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def list_voices() -> list[dict]:
    if not VOICES_DIR.exists():
        return []
    out = []
    for p in sorted(VOICES_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


# ──────────────────────────────────────────────
# prompt 특징 빌드 / 캐시
# ──────────────────────────────────────────────
def build_prompt(model, ref_audio: str, ref_text: str):
    """참조 음성에서 prompt 특징을 1회 계산한다 (이후 합성에서 재사용)."""
    print("[TTS] 참조 prompt 특징 계산 중...")
    return model.create_voice_clone_prompt(
        ref_audio=ref_audio,
        ref_text=ref_text,
        x_vector_only_mode=False,
    )


def get_or_build_prompt(model, profile: dict, device_map: str, use_cache: bool = True):
    """등록된 음성의 prompt 특징을 디스크 캐시에서 로드하거나 새로 계산해 캐시한다.

    캐시는 best-effort다 — 라이브러리 버전이 바뀌어 로드가 실패하면 자동 재계산한다.
    """
    import torch

    cache_path = VOICES_DIR / f"{profile['name']}.prompt.pt"

    if use_cache and cache_path.exists():
        try:
            prompt = torch.load(cache_path, map_location=device_map, weights_only=False)
            print(f"[TTS] prompt 캐시 재사용: {cache_path.name}")
            return prompt
        except Exception as e:
            print(f"[TTS] prompt 캐시 로드 실패 → 재계산 ({type(e).__name__})")

    prompt = build_prompt(model, profile["ref_audio"], profile["ref_text"])

    if use_cache:
        try:
            torch.save(prompt, cache_path)
            print(f"[TTS] prompt 캐시 저장: {cache_path.name}")
        except Exception as e:
            print(f"[TTS] prompt 캐시 저장 생략 ({type(e).__name__})")
    return prompt


# ──────────────────────────────────────────────
# 텍스트 청킹 (긴 입력을 문장 경계에서 분할)
# ──────────────────────────────────────────────
def _hard_split(s: str, max_chars: int) -> list[str]:
    """한 문장이 max_chars를 넘으면 쉼표/공백, 최후엔 글자 단위로 강제 분할."""
    parts: list[str] = []
    cur = ""
    for tok in re.split(r'(?<=[,，、])\s*|\s+', s):
        if not tok:
            continue
        if len(tok) > max_chars:
            if cur:
                parts.append(cur); cur = ""
            for i in range(0, len(tok), max_chars):
                parts.append(tok[i:i + max_chars])
            continue
        if cur and len(cur) + 1 + len(tok) > max_chars:
            parts.append(cur); cur = tok
        else:
            cur = f"{cur} {tok}".strip() if cur else tok
    if cur:
        parts.append(cur)
    return parts


def split_text(text: str, max_chars: int) -> list[str]:
    """문장 종결부호/줄바꿈 경계에서 텍스트를 max_chars 이하 청크들로 묶는다.
    max_chars <= 0 이면 분할하지 않는다."""
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    # 종결부호(. ! ? 。 … )와 줄바꿈을 경계로 분할 (구분자는 앞 문장에 유지)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?。…\n])\s*', text) if s.strip()]

    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(s) > max_chars:                      # 한 문장이 너무 길면 추가 분할
            if cur:
                chunks.append(cur); cur = ""
            chunks.extend(_hard_split(s, max_chars))
            continue
        if cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur); cur = s
        else:
            cur = f"{cur} {s}".strip() if cur else s
    if cur:
        chunks.append(cur)
    return chunks


# ──────────────────────────────────────────────
# 합성 / 저장
# ──────────────────────────────────────────────
def synthesize(model, text: str, language: str, prompt,
               max_chars: int, gap_sec: float):
    """prompt를 재사용해 text를 합성한다. 긴 텍스트는 청크 단위로 순차 처리해
    피크 GPU 메모리를 청크 1개 분량으로 제한한다. 반환: (audio_ndarray, sample_rate)."""
    import numpy as np

    # CUDA 가용 시 청크 사이 캐시 정리 / 추론 모드로 메모리 절감
    try:
        import torch
        has_cuda = torch.cuda.is_available()
        inference_ctx = torch.inference_mode
    except Exception:
        from contextlib import nullcontext
        has_cuda = False
        inference_ctx = nullcontext

    chunks = split_text(text, max_chars)
    n = len(chunks)
    if n > 1:
        print(f"[TTS] 텍스트 {len(text)}자 → {n}개 청크로 순차 합성 (max_chars={max_chars})")
    else:
        print(f"[TTS] 단일 청크 합성 ({len(text)}자)")

    parts: list = []
    sr = None
    silence = None
    for i, chunk in enumerate(chunks, 1):
        c_start = time.time()
        with inference_ctx():
            wavs, cur_sr = model.generate_voice_clone(
                text=chunk, language=language, voice_clone_prompt=prompt,
            )
        wav = wavs[0]
        if sr is None:
            sr = cur_sr
            if gap_sec > 0:                          # 청크 사이 무음 (자연스러운 끊김)
                gap_len = int(sr * gap_sec)
                silence = (np.zeros((gap_len, wav.shape[1]), dtype=wav.dtype)
                           if wav.ndim == 2 else np.zeros(gap_len, dtype=wav.dtype))
        if parts and silence is not None:
            parts.append(silence)
        parts.append(wav)
        print(f"[TTS]  ({i}/{n}) {len(chunk)}자 → {time.time() - c_start:.1f}초")
        if has_cuda:
            torch.cuda.empty_cache()

    audio = np.concatenate(parts, axis=0) if len(parts) > 1 else parts[0]
    return audio, sr


def save_audio(audio, sr: int, out_path: Path) -> Path:
    """합성된 오디오 배열을 저장한다. 확장자가 .mp3면 ffmpeg로 mp3 변환, 그 외는 wav로 저장."""
    try:
        import soundfile as sf
    except ImportError as e:
        raise RuntimeError(f"soundfile이 설치되지 않았습니다 (pip install soundfile): {e}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.suffix.lower() != ".mp3":
        sf.write(str(out_path), audio, sr)
        return out_path

    # mp3: 임시 wav로 저장 후 ffmpeg로 변환 (프로젝트 공통 의존성인 ffmpeg 사용)
    tmp_wav = out_path.with_name(f"{out_path.stem}.tmp.wav")
    sf.write(str(tmp_wav), audio, sr)
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(tmp_wav),
            "-codec:a", "libmp3lame", "-q:a", "2",
            "-loglevel", "error", str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(
                "ffmpeg mp3 변환 실패 (ffmpeg 설치 여부 확인). "
                f"--output을 .wav로 지정하면 변환 없이 저장됩니다.\n{result.stderr}"
            )
    finally:
        try:
            tmp_wav.unlink()
        except OSError:
            pass
    return out_path


# ──────────────────────────────────────────────
# 서브커맨드 핸들러
# ──────────────────────────────────────────────
def cmd_register(args) -> int:
    ref_text = resolve_ref_text(
        args.ref_text, args.ref_text_file, args.auto_ref_text,
        args.ref_audio, args.language,
    )
    profile = register_voice(args.name, args.ref_audio, ref_text)
    print(f"\n[등록 완료] '{profile['name']}'")
    print(f"  참조 음성: {profile['ref_audio']}")
    print(f"  참조 텍스트: {profile['ref_text'][:80]}{'...' if len(profile['ref_text']) > 80 else ''}")
    print(f"\n이제 다음처럼 재사용하세요:")
    print(f'  python voice_clone.py generate --voice {profile["name"]} --text "합성할 문장"')
    return 0


def cmd_list(args) -> int:
    voices = list_voices()
    if not voices:
        print("등록된 음성이 없습니다. register로 먼저 등록하세요.")
        return 0
    print(f"등록된 음성 {len(voices)}개:")
    for v in voices:
        snippet = (v.get("ref_text", "") or "")[:50]
        print(f"  • {v['name']}  (등록: {v.get('created', '?')})  \"{snippet}...\"")
    return 0


def cmd_remove(args) -> int:
    removed = []
    for suffix in [".json", ".prompt.pt"]:
        p = VOICES_DIR / f"{args.name}{suffix}"
        if p.exists():
            p.unlink(); removed.append(p.name)
    for p in VOICES_DIR.glob(f"{args.name}.ref.*"):
        p.unlink(); removed.append(p.name)
    if not removed:
        print(f"삭제할 음성이 없습니다: '{args.name}'")
        return 1
    print(f"[삭제] {', '.join(removed)}")
    return 0


def cmd_generate(args) -> int:
    start = time.time()  # 음성 생성 총 소요시간 측정 시작

    # 1. 생성할 텍스트 확보
    if args.text is not None:
        text = args.text
    elif args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
    else:
        raise ValueError("생성할 텍스트가 필요합니다 (--text / --text-file).")
    if not text:
        raise ValueError("생성할 텍스트가 비어 있습니다.")

    # 2. 음성 소스 결정: 등록된 voice 또는 즉석 ref
    if args.voice:
        profile = load_voice(args.voice)
        out_stem = args.voice
    else:
        if not args.ref_audio:
            raise ValueError("--voice 또는 --ref-audio 중 하나가 필요합니다.")
        ref_text = resolve_ref_text(
            args.ref_text, args.ref_text_file, args.auto_ref_text,
            args.ref_audio, args.language,
        )
        profile = {"name": None, "ref_audio": args.ref_audio, "ref_text": ref_text}
        out_stem = Path(args.ref_audio).stem or "ref"

    # 3. 출력 경로 결정
    #    --output 지정 시: 그 경로 그대로 사용 (확장자 .mp3/.wav 모두 가능)
    #    미지정 시: --text-file이 있으면 그 파일이 위치한 폴더에,
    #              파일명은 {voice}_{텍스트파일이름}_MMDD_HHMMSS.mp3
    ts = datetime.now().strftime("%m%d_%H%M%S")
    if args.output:
        out_path = Path(args.output).resolve()
    else:
        if args.text_file:
            tf = Path(args.text_file)
            folder = tf.parent
            text_label = tf.stem
        else:
            folder = Path(TTS_OUTPUT_DIR)
            text_label = "text"
        out_path = (folder / f"{out_stem}_{text_label}_{ts}.wav").resolve()

    # 4. 모델 로드
    print(f"\n{'='*60}")
    model, device_map = load_model(args.model, args.device, args.attn)

    # 5. prompt 확보 (등록 음성이면 디스크 캐시 사용, 즉석이면 인메모리)
    print(f"\n{'='*60}")
    if profile["name"]:
        prompt = get_or_build_prompt(model, profile, device_map, use_cache=not args.no_cache)
    else:
        prompt = build_prompt(model, profile["ref_audio"], profile["ref_text"])

    # 6. 합성 (긴 텍스트는 청크 단위 순차 처리 → 피크 메모리 제한) + 저장
    audio, sr = synthesize(model, text, args.language, prompt, args.max_chars, args.chunk_gap)
    saved = save_audio(audio, sr, out_path)

    elapsed = time.time() - start
    mins, sec = divmod(elapsed, 60)
    print(f"\n{'='*60}")
    print(f"[SAVE] {saved}")
    print(f"[TIME] 총 소요시간: {int(mins)}분 {sec:.1f}초")
    print("완료!")
    return 0


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def _add_model_args(sp):
    sp.add_argument("--model", default=QWEN_TTS_MODEL, help=f"Qwen3-TTS 모델 (기본: {QWEN_TTS_MODEL}).")
    sp.add_argument("--device", default="auto", help="device_map: auto | cuda:0 | cpu (기본: auto).")
    sp.add_argument(
        "--attn", default="auto",
        choices=["auto", "flash_attention_2", "sdpa", "eager"],
        help="attention 구현 (기본: auto - CUDA=sdpa, CPU=eager).",
    )


def _add_ref_args(sp):
    sp.add_argument("--ref-text", default=None, help="참조 음성의 정확한 발화 내용.")
    sp.add_argument("--ref-text-file", default=None, help="참조 텍스트 .txt 파일 경로.")
    sp.add_argument(
        "--auto-ref-text", action="store_true",
        help="참조 텍스트를 faster-whisper로 자동 추출 (로컬 오디오만).",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Qwen3-TTS 음성 클로닝 - 음성을 등록해두고 이름으로 재사용",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # register
    sp_reg = sub.add_parser("register", help="음성 프로필 등록 (한 번만)")
    sp_reg.add_argument("--name", required=True, help="음성 이름 (재사용 시 사용).")
    sp_reg.add_argument("--ref-audio", required=True, help="참조 음성 (로컬 경로/URL/base64). 3~15초 권장.")
    _add_ref_args(sp_reg)
    sp_reg.add_argument("--language", default="Korean", choices=SUPPORTED_LANGUAGES,
                        help="참조 텍스트 자동 추출 시 언어 힌트 (기본: Korean).")
    sp_reg.set_defaults(func=cmd_register)

    # list
    sp_list = sub.add_parser("list", help="등록된 음성 목록")
    sp_list.set_defaults(func=cmd_list)

    # remove
    sp_rm = sub.add_parser("remove", help="등록된 음성 삭제")
    sp_rm.add_argument("--name", required=True, help="삭제할 음성 이름.")
    sp_rm.set_defaults(func=cmd_remove)

    # generate
    sp_gen = sub.add_parser("generate", help="텍스트를 음성으로 합성")
    sp_gen.add_argument("--voice", default=None, help="등록된 음성 이름 (지정 시 ref 인자 불필요).")
    sp_gen.add_argument("--ref-audio", default=None, help="즉석 모드: 참조 음성 경로/URL/base64.")
    _add_ref_args(sp_gen)
    sp_gen.add_argument("--text", default=None, help="합성할 텍스트.")
    sp_gen.add_argument("--text-file", "--textfile", dest="text_file", default=None,
                        help="합성할(클로닝할) 텍스트를 담은 .txt 파일.")
    sp_gen.add_argument("--language", default="Korean", choices=SUPPORTED_LANGUAGES,
                        help="합성 언어 (기본: Korean).")
    sp_gen.add_argument("--output", "-o", default=None, help="결과 wav 경로.")
    sp_gen.add_argument("--max-chars", type=int, default=200,
                        help="청크당 최대 글자 수. 긴 텍스트를 문장 단위로 분할해 순차 합성 "
                             "(피크 GPU 메모리 제한). 0이면 분할 안 함 (기본: 200).")
    sp_gen.add_argument("--chunk-gap", type=float, default=0.3,
                        help="청크 사이 무음 길이(초) (기본: 0.3).")
    sp_gen.add_argument("--no-cache", action="store_true", help="prompt 디스크 캐시 사용 안 함.")
    _add_model_args(sp_gen)
    sp_gen.set_defaults(func=cmd_generate)

    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
