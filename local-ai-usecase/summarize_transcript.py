"""
스크립트(전체 자막) → 정리된 마크다운 요약 생성기 (Local LLM / Ollama)

긴 스크립트도 잘림 없이 정리하기 위해 map-reduce 방식을 사용한다.
  1) map   : 자막을 청크로 나눠 각 청크의 핵심 노트를 충실하게 추출
  2) reduce: 추출된 노트를 합쳐 선택한 프롬프트 형식으로 최종 정리

요약 결과는 입력 파일과 같은 위치에 같은 이름(.md)으로 저장된다.

사용 예:
  python summarize_transcript.py "transcripts/[월가아재] ... 진짜 이유.txt"
  python summarize_transcript.py 자막.txt --model gemma4:12b --prompt outline
  python summarize_transcript.py 자막.txt --prompt-file my_prompt.txt
  python summarize_transcript.py --list-models
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows 콘솔(cp949)에서 한글/이모지 출력이 깨지지 않도록 UTF-8 고정
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
DEFAULT_HOST = "http://localhost:11434"   # Ollama 서버 (환경에 맞게 변경)
DEFAULT_MODEL = "gemma4:12b"                    # 품질 우선 기본값. 빠르게는 --model qwen3:8b
DEFAULT_CHUNK_CHARS = 4000                     # map 단계 청크 크기(문자)
DEFAULT_NUM_CTX = 16384                        # 컨텍스트 토큰(프롬프트+출력 합산. 작으면 결과가 잘림)

# ──────────────────────────────────────────────
# 최종(reduce) 프롬프트 템플릿 — --prompt 로 선택
#   {title}, {content} 자리표시자를 채워서 사용한다.
# ──────────────────────────────────────────────
PROMPTS = {
    # 기본: 과하게 줄이지 않고, 주요 내용을 제대로 이해할 수 있게 정리
    "detailed": (
        "다음은 유튜브 영상 '{title}'의 전체 자막(또는 그 핵심 노트)입니다.\n"
        "이것을 한국어로 '정리 노트' 형태로 재구성하세요. 목표는 영상을 보지 않아도 "
        "주요 내용과 핵심 논리를 충분히 이해하는 것입니다. 너무 짧게 줄이지 말고, "
        "중요한 근거·수치·인물·용어·인과관계는 빠짐없이 담되, 군더더기와 반복은 제거하세요.\n\n"
        "형식:\n"
        "## 📌 한 줄 요약\n"
        "(이 영상이 다루는 핵심을 한 문장으로)\n\n"
        "## 🧭 전체 맥락\n"
        "(왜 이 주제를 다루는지, 배경과 문제의식 2~4문장)\n\n"
        "## 📋 핵심 내용\n"
        "(주제별로 나누되 각 소제목은 '### 제목' 한 줄로 시작(### 기호는 한 번만, 번호 매기지 말 것), "
        "그 아래 - 불릿으로 상세 정리. 주장→근거→사례/수치 순서가 드러나게. 필요한 만큼 충분히)\n\n"
        "## 💡 핵심 포인트 / 시사점\n"
        "(시청자가 얻어갈 가장 중요한 인사이트 3~6개를 - 불릿으로)\n\n"
        "## 🔑 주요 용어\n"
        "(영상에 나온 핵심 개념·용어를 '용어: 한 줄 설명'으로. 없으면 생략)\n\n"
        "자막/노트:\n{content}\n\n정리 결과(마크다운):"
    ),
    # 계층형 개요 위주
    "outline": (
        "다음은 유튜브 영상 '{title}'의 전체 자막(또는 핵심 노트)입니다.\n"
        "내용을 한국어 계층형 개요(아웃라인)로 정리하세요. 대주제는 ## , 소주제는 ### , "
        "세부는 - 불릿으로. 핵심 수치·근거는 불릿에 포함하고, 과하게 압축하지 마세요.\n\n"
        "자막/노트:\n{content}\n\n개요(마크다운):"
    ),
    # 더 짧은 버전
    "brief": (
        "다음은 유튜브 영상 '{title}'의 전체 자막(또는 핵심 노트)입니다.\n"
        "핵심만 한국어로 간결하게 정리하세요: ① 한 줄 요약 ② 핵심 내용 5~8개 불릿 "
        "③ 시사점 3개. 다만 이해에 꼭 필요한 근거·수치는 빠뜨리지 마세요.\n\n"
        "자막/노트:\n{content}\n\n요약(마크다운):"
    ),
}

# map 단계: 각 청크에서 사실을 충실히 추출(스타일링 없이)
MAP_PROMPT = (
    "다음은 한 유튜브 영상 자막의 일부분(파트 {idx}/{total})입니다.\n"
    "이 부분에서 말한 내용을 한국어 불릿 노트로 '충실하게' 추출하세요. "
    "주장, 근거, 수치, 인물·기관명, 사례, 인과관계를 빠짐없이 적되 군더더기·인사말·잡담은 빼세요. "
    "요약하지 말고 정보를 보존하는 것이 목적입니다. 다른 말 없이 불릿만 출력하세요.\n\n"
    "자막 일부:\n{chunk}\n\n불릿 노트:"
)


# ──────────────────────────────────────────────
# Ollama 호출
# ──────────────────────────────────────────────
def list_models(host: str) -> list[str]:
    req = urllib.request.Request(f"{host}/api/tags")
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.load(r)
    return [m["name"] for m in data.get("models", [])]


def ollama_generate(host: str, model: str, prompt: str, *,
                    num_ctx: int, temperature: float, timeout: int = 600) -> tuple[str, float]:
    """Ollama /api/generate 호출 → (응답텍스트, 소요초)."""
    # qwen3 계열은 /no_think 로 사고과정 출력을 끌 수 있다.
    if model.lower().startswith("qwen3") and "/no_think" not in prompt:
        prompt = prompt + " /no_think"

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{host}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        result = json.load(r)

    text = result.get("response", "").strip()
    elapsed = result.get("total_duration", 0) / 1e9
    return strip_think(text), elapsed


def strip_think(text: str) -> str:
    """<think>...</think> 블록과 빈 /no_think 잔재를 제거한다."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.replace("/no_think", "")
    return text.strip()


def normalize_md(text: str) -> str:
    """모델별 마크다운 잔재를 결정적으로 교정한다.
    - '### ### 제목' 처럼 헤더 마커를 겹쳐 찍는 것 → 한 번으로
    - 줄머리 '*' 불릿(gemma 경향) → '-' 로 통일 (들여쓰기·굵게(**)는 보존)
    """
    text = re.sub(r"(?m)^(#{1,6})\s+#{1,6}\s+", r"\1 ", text)
    text = re.sub(r"(?m)^(\s*)\*[ \t]+", r"\1- ", text)
    return text


# ──────────────────────────────────────────────
# 입력 파싱 / 청킹
# ──────────────────────────────────────────────
def parse_transcript(path: Path) -> tuple[dict, str]:
    """txt에서 헤더(# title/url/channel/language)와 본문을 분리한다."""
    raw = path.read_text(encoding="utf-8-sig")
    meta = {"title": path.stem, "url": "", "channel": "", "language": ""}
    body_lines: list[str] = []
    title_locked = False

    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("#"):
            content = s.lstrip("#").strip()
            low = content.lower()
            if low.startswith("영상주소") or low.startswith("url") or "youtube.com/watch" in low:
                meta["url"] = content.split(":", 1)[-1].strip() if ":" in content else content
            elif low.startswith("채널") or low.startswith("channel"):
                meta["channel"] = content.split(":", 1)[-1].strip()
            elif low.startswith("language") or low.startswith("언어"):
                meta["language"] = content.split(":", 1)[-1].strip()
            elif not title_locked and content:
                meta["title"] = content   # 첫 번째 # 줄을 제목으로
                title_locked = True
        else:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    return meta, body


def chunk_text(body: str, chunk_chars: int) -> list[str]:
    """줄 경계를 지키며 chunk_chars 이하 청크로 나눈다."""
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in body.splitlines():
        ln = len(line) + 1
        if cur_len + ln > chunk_chars and cur:
            chunks.append("\n".join(cur).strip())
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += ln
    if cur:
        chunks.append("\n".join(cur).strip())
    return [c for c in chunks if c]


# ──────────────────────────────────────────────
# 요약 파이프라인
# ──────────────────────────────────────────────
def summarize(body: str, title: str, *, host: str, model: str, final_template: str,
              chunk_chars: int, num_ctx: int, temperature: float) -> str:
    chunks = chunk_text(body, chunk_chars)
    print(f"[CHUNK] 본문 {len(body)}자 → {len(chunks)}개 청크 (청크당 ~{chunk_chars}자)")

    # 1개면 map 생략, 바로 최종 정리
    if len(chunks) <= 1:
        content = body
    else:
        notes: list[str] = []
        for i, ch in enumerate(chunks, 1):
            prompt = MAP_PROMPT.format(idx=i, total=len(chunks), chunk=ch)
            note, sec = ollama_generate(host, model, prompt,
                                        num_ctx=num_ctx, temperature=temperature)
            print(f"  [MAP {i}/{len(chunks)}] {len(note)}자 추출 ({sec:.1f}s)")
            notes.append(f"### 파트 {i}\n{note}")
        content = "\n\n".join(notes)

    print(f"[REDUCE] 최종 정리 생성 중 ... (model={model})")
    final_prompt = final_template.format(title=title, content=content)
    summary, sec = ollama_generate(host, model, final_prompt,
                                   num_ctx=num_ctx, temperature=temperature,
                                   timeout=900)
    print(f"[REDUCE] 완료 ({sec:.1f}s, {len(summary)}자)")
    return normalize_md(summary)


def build_markdown(meta: dict, model: str, summary: str) -> str:
    head = [f"# {meta['title']} — 요약", ""]
    if meta.get("url"):
        head.append(f"- **원본**: {meta['url']}")
    if meta.get("channel"):
        head.append(f"- **채널**: {meta['channel']}")
    head.append(f"- **요약 모델**: {model} (Ollama)")
    head.append("")
    head.append("---")
    head.append("")
    return "\n".join(head) + summary + "\n"


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def resolve_input(name: str) -> Path:
    p = Path(name)
    if p.exists():
        return p
    # transcripts/ 아래에서도 찾아본다
    alt = Path("transcripts") / name
    if alt.exists():
        return alt
    raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {name}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="스크립트(자막) → 정리된 마크다운 요약 (Local LLM / Ollama)")
    ap.add_argument("file", nargs="?", help="자막 txt 경로(또는 transcripts/ 내 파일명)")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"Ollama 호스트 (기본 {DEFAULT_HOST})")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"모델 (기본 {DEFAULT_MODEL})")
    ap.add_argument("--prompt", default="detailed", choices=list(PROMPTS),
                    help="내장 프롬프트 템플릿 선택 (기본 detailed)")
    ap.add_argument("--prompt-file", help="사용자 프롬프트 파일(.txt). {title},{content} 자리표시자 사용")
    ap.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS,
                    help=f"map 청크 크기 문자수 (기본 {DEFAULT_CHUNK_CHARS})")
    ap.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX,
                    help=f"컨텍스트 토큰 (기본 {DEFAULT_NUM_CTX})")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("-o", "--output", help="출력 .md 경로 (기본: 입력과 같은 위치/이름)")
    ap.add_argument("--list-models", action="store_true", help="서버의 모델 목록만 출력")
    args = ap.parse_args()

    # 서버 확인
    try:
        models = list_models(args.host)
    except urllib.error.URLError as e:
        print(f"❌ Ollama 서버 연결 실패: {args.host}\n   {e}")
        return 2

    if args.list_models:
        print(f"[{args.host}] 사용 가능한 모델:")
        for m in models:
            print(f"  - {m}")
        return 0

    if not args.file:
        ap.error("자막 파일 경로가 필요합니다 (또는 --list-models)")

    if args.model not in models and not any(args.model in m for m in models):
        print(f"⚠️  '{args.model}' 모델이 서버 목록에 없습니다. 설치된 모델: {models}")
        print(f"   계속 진행하지만 실패할 수 있습니다 (필요시: ollama pull {args.model})")

    # 프롬프트 템플릿 결정
    if args.prompt_file:
        template = Path(args.prompt_file).read_text(encoding="utf-8")
        if "{content}" not in template:
            print("⚠️  프롬프트 파일에 {content} 자리표시자가 없습니다 — 본문이 삽입되지 않습니다.")
    else:
        template = PROMPTS[args.prompt]

    in_path = resolve_input(args.file)
    meta, body = parse_transcript(in_path)
    if not body:
        print(f"❌ 본문이 비어 있습니다: {in_path}")
        return 2

    print(f"=== 요약 시작 ===")
    print(f"입력 : {in_path}")
    print(f"제목 : {meta['title']}")
    print(f"모델 : {args.model}  | 프롬프트: {args.prompt_file or args.prompt}  | 호스트: {args.host}")

    t0 = time.time()
    summary = summarize(
        body, meta["title"],
        host=args.host, model=args.model, final_template=template,
        chunk_chars=args.chunk_chars, num_ctx=args.num_ctx, temperature=args.temperature,
    )

    out_path = Path(args.output) if args.output else in_path.with_suffix(".md")
    out_path.write_text(build_markdown(meta, args.model, summary), encoding="utf-8")
    print(f"\n✅ 저장 완료: {out_path}  (총 {time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
