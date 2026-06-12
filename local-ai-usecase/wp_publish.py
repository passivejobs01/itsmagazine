"""
WordPress 자동 발행 헬퍼 (순수 유틸 — async/HTTP 없음).

fastapi_linux가 이 모듈의 프롬프트·파서·임베드·중복관리를 가져다 쓰고,
실제 REST 호출(POST 글 생성)은 fastapi 쪽 async httpx로 수행한다.
"""

import json
import re
from pathlib import Path

# ──────────────────────────────────────────────
# 블로그 글 생성용 reduce 프롬프트 ({title}, {content})
#   _summarize_with_ollama(final_template=BLOG_TEMPLATE)로 재사용
# ──────────────────────────────────────────────
BLOG_TEMPLATE = (
    "다음은 유튜브 영상 '{title}'의 자막(또는 핵심 노트)입니다.\n"
    "이것을 한국어 '블로그 글'로 재구성하세요. 구어체 자막을 매끄러운 문어체로 다듬되, "
    "핵심 정보·근거·예시·수치는 보존하고 군더더기·반복·인사말은 제거합니다.\n\n"
    "반드시 아래 형식(마크다운)으로만 출력하세요:\n"
    "# (영상 내용을 잘 드러내는 매력적인 제목 — 과장·낚시 금지)\n\n"
    "(도입부 2~3문장: 이 글에서 무엇을 얻는지)\n\n"
    "## (소제목)\n(본문 단락)\n\n"
    "## (소제목)\n(본문 단락)\n\n"
    "(주제에 맞게 소제목을 필요한 만큼)\n\n"
    "## 마무리\n(핵심 요점 정리)\n\n"
    "태그: 키워드1, 키워드2, 키워드3, 키워드4, 키워드5\n\n"
    "규칙: 영상 기반임을 자연스럽게 녹이고, 정보는 빠짐없이, 전부 한국어로. "
    "맨 첫 줄은 반드시 '# 제목', 맨 끝 줄은 반드시 '태그: ...' 형식.\n\n"
    "자막/노트:\n{content}\n\n블로그 글(마크다운):"
)

# 발행 기록(중복 방지): video_id -> {post_id, link}
PUBLISHED_DB = Path(__file__).parent / "wp_published.json"


def youtube_id(url: str) -> str:
    """YouTube URL에서 video_id(11자) 추출. 실패 시 빈 문자열."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})", url)
    return m.group(1) if m else ""


def parse_blog_markdown(md: str) -> tuple[str, str, list[str]]:
    """LLM 마크다운 출력에서 (제목, 본문 마크다운, 태그목록)을 분리한다."""
    title = ""
    tags: list[str] = []
    body: list[str] = []
    for line in md.strip().splitlines():
        s = line.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if (s.startswith("태그:") or s.lower().startswith("tags:")) and not tags:
            tagstr = s.split(":", 1)[1]
            tags = [t.strip() for t in re.split(r"[,，]", tagstr) if t.strip()]
            continue
        body.append(line)
    return (title or "제목 없음"), "\n".join(body).strip(), tags


def md_to_html(md: str) -> str:
    """본문 마크다운 → HTML. markdown 패키지 우선, 없으면 단순 폴백."""
    try:
        import markdown as _md
        return _md.markdown(md, extensions=["extra", "sane_lists"])
    except ImportError:
        paras = [p.strip() for p in md.split("\n\n") if p.strip()]
        return "\n".join(f"<p>{p}</p>" for p in paras)


def youtube_embed_block(url: str) -> str:
    """Gutenberg YouTube 임베드 블록 마크업."""
    return (
        '<!-- wp:embed {"url":"' + url + '","type":"video","providerNameSlug":"youtube",'
        '"responsive":true,"className":"wp-embed-aspect-16-9 wp-has-aspect-ratio"} -->\n'
        '<figure class="wp-block-embed is-type-video is-provider-youtube wp-block-embed-youtube '
        'wp-embed-aspect-16-9 wp-has-aspect-ratio"><div class="wp-block-embed__wrapper">\n'
        + url + "\n</div></figure>\n<!-- /wp:embed -->"
    )


def build_post_html(youtube_url: str, body_html: str) -> str:
    """상단 유튜브 임베드 + 본문 HTML."""
    return youtube_embed_block(youtube_url) + "\n\n" + body_html


# ── 중복 방지 기록 ──
def load_published() -> dict:
    if PUBLISHED_DB.exists():
        try:
            return json.loads(PUBLISHED_DB.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def is_published(video_id: str) -> dict | None:
    """이미 발행됐으면 {post_id, link} 반환, 아니면 None."""
    return load_published().get(video_id)


def mark_published(video_id: str, post_id: int, link: str) -> None:
    d = load_published()
    d[video_id] = {"post_id": post_id, "link": link}
    PUBLISHED_DB.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
