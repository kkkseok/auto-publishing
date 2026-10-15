"""초안 발행 — 승인 없이 바로 발행한다. blog_drafts_daily 가 호출하고, 수동 재시도도 된다.

    python -m pipelines.blog_job publish <draft_id> [--private]

모든 발행은 Aside 브라우저로 한다:
  A·B 네이버   → publishers/naver_aside
  C·D 티스토리 → publishers/tistory_aside (DKAPTCHA 는 텔레그램으로 사람에게 묻는다)

사람이 보지 않으므로 check_draft() 의 품질 기준을 통과하지 못한 초안은 발행하지 않는다.
이미지는 아직 올리지 않는다 — 본문만 발행.
"""
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts
from common.draft_writer import render_html
from common.keyword_planner import info_category
from common.logger import log
from common.publish_queue import add_url

NAVER = {"A": ("NAVER_BLOG_ID_INFO", "NAVER_CATEGORY_INFO", "NAVER_ASIDE_PROFILE_INFO"),
         "B": ("NAVER_BLOG_ID_PRICE", "NAVER_CATEGORY_PRICE", "NAVER_ASIDE_PROFILE_PRICE")}
TISTORY_ROLE = {"C": "info", "D": "price"}
MIN_CHARS = 1500


def _visible_text(draft: dict) -> str:
    """독자가 읽는 글자 — 문단·표·단계·체크리스트·FAQ·요약·맺음말."""
    parts = [draft.get("intro", ""), draft.get("closing", ""), *draft.get("summary_points", [])]
    for s in draft.get("sections") or []:
        parts += s.get("paragraphs") or []
        parts += [c for row in s.get("table_rows") or [] for c in row]
        parts += (s.get("steps") or []) + (s.get("checklist") or []) + [s.get("callout_text", "")]
    for f in draft.get("faq") or []:
        parts += [f.get("q", ""), f.get("a", "")] if isinstance(f, dict) else [str(f)]
    return " ".join(str(p) for p in parts if p)


def check_draft(draft: dict) -> list[str]:
    """자동 발행 전 품질 기준. 걸린 항목 목록(비면 통과)."""
    problems = []
    if not draft.get("title", "").strip():
        problems.append("제목 없음")
    if len(draft.get("sections") or []) < 4:
        problems.append(f"소제목 {len(draft.get('sections') or [])}개 (4개 미만)")
    if not draft.get("sources"):
        problems.append("출처 없음")
    text = json.dumps(draft, ensure_ascii=False)
    if re.search(r"확인\s*필요|\[TODO|TBD|lorem", text, re.I):
        problems.append("미확인 표시가 남아 있음")
    plain = _visible_text(draft)
    if len(plain) < MIN_CHARS:
        problems.append(f"본문 {len(plain)}자 ({MIN_CHARS}자 미만)")
    return problems


def _fail(draft_id: str, title: str, msg: str) -> bool:
    blog_drafts.update(draft_id, status="failed", error=msg[:500])
    blog_drafts.notify(f"❌ 발행 실패: {title}\n{msg[:300]}")
    log(f"[블로그발행] 실패 {draft_id}: {msg}", "error")
    return False


def publish(draft_id: str, private: bool = False) -> bool:
    item = blog_drafts.get(draft_id)
    if not item:
        raise SystemExit(f"초안 없음: {draft_id}")
    blog = item["blog"]
    draft = json.loads((Path(item["dir"]) / f"{blog}_draft.json").read_text(encoding="utf-8"))
    problems = check_draft(draft)
    if problems:
        return _fail(draft_id, draft["title"], "품질 기준 미달 — " + ", ".join(problems))
    body = render_html(draft, {}, blog)
    blog_drafts.update(draft_id, status="publishing")
    if blog in NAVER:
        from publishers.naver_aside import publish as naver_publish
        id_env, cat_env, prof_env = NAVER[blog]
        profile = os.getenv(prof_env, "")
        if blog == "B" and not profile:
            return _fail(draft_id, draft["title"], "블로그 B 는 별도 Aside 프로필 필요 (NAVER_ASIDE_PROFILE_PRICE 미설정)")
        category = (info_category(item["keyword"]) if blog == "A" else "") or os.getenv(cat_env, "")
        r = naver_publish(os.getenv(id_env, ""), draft["title"], body, draft["tags"],
                          category=category, private=private, profile=profile)
        platform = "naver"
    else:
        from common.tistory_blogs import resolve_blog_name
        from publishers.tistory_aside import publish as tistory_publish
        r = tistory_publish(resolve_blog_name(TISTORY_ROLE[blog]), draft["title"], body, draft["tags"],
                            item_id=draft_id, private=private)
        platform = "tistory"
    if not r.success:
        return _fail(draft_id, draft["title"], r.message)
    blog_drafts.update(draft_id, status="published", url=r.url, private=private)
    if not private:
        add_url(r.url, platform=platform, title=draft["title"], keyword=item["keyword"], source="blog_draft")
    blog_drafts.notify(f"✅ [{blog}] {'비공개 ' if private else ''}발행 완료\n{draft['title']}\n{r.url}")
    log(f"[블로그발행] 발행 완료 {r.url}", "ok")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if len(sys.argv) < 3 or sys.argv[1] != "publish":
        raise SystemExit("usage: python -m pipelines.blog_job publish <draft_id> [--private]")
    ok = publish(sys.argv[2], "--private" in sys.argv)
    sys.exit(0 if ok else 1)
