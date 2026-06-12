"""
중단된 요약 이어서 진행: summaries/*.md 의 '## 전체 스크립트'에서 자막을 읽어
map-reduce 요약을 만들고 '## 요약' 섹션을 삽입한다. (STT 재실행 불필요)

사용: python resume_summary.py "summaries/<파일>.md"
"""
import os
import sys
from pathlib import Path

import summarize_transcript as st

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL       = os.getenv("SUMMARY_MODEL", "gemma4:12b")
NUM_CTX     = int(os.getenv("SUMMARY_NUM_CTX", "16384"))
CHUNK       = int(os.getenv("SUMMARY_CHUNK_CHARS", "4000"))
MARKER      = "## 전체 스크립트"


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python resume_summary.py <md경로>"); return 2
    md_path = Path(sys.argv[1])
    md = md_path.read_text(encoding="utf-8")

    if MARKER not in md:
        print(f"[ERR] '{MARKER}' 섹션이 없습니다: {md_path}"); return 1
    head, transcript = md.split(MARKER, 1)
    transcript = transcript.strip()
    if "## 요약" in head:
        print("[SKIP] 이미 '## 요약' 섹션이 있습니다."); return 0
    if not transcript:
        print("[ERR] 자막이 비어 있습니다."); return 1

    title = md_path.stem
    for line in md.splitlines():
        if line.startswith("# "):
            title = line[2:].strip(); break

    print(f"[RESUME] {title}")
    print(f"[RESUME] 자막 {len(transcript)}자 → 요약 시작 (model={MODEL}, host={OLLAMA_HOST})")
    summary = st.summarize(
        transcript, title,
        host=OLLAMA_HOST, model=MODEL,
        final_template=st.PROMPTS["detailed"],
        chunk_chars=CHUNK, num_ctx=NUM_CTX, temperature=0.3,
    )
    if not summary.strip():
        print("[ERR] 요약 결과가 비어 있습니다."); return 1

    new_md = (head.rstrip() + "\n\n## 요약\n\n" + summary.strip()
              + "\n\n" + MARKER + "\n\n" + transcript + "\n")
    md_path.write_text(new_md, encoding="utf-8")
    print(f"[DONE] '## 요약' 삽입 완료 ({len(summary)}자) → {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
