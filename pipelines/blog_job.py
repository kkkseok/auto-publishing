"""초안 발행 — 승인 없이 바로 발행한다. blog_drafts_daily 가 호출하고, 수동 재시도도 된다.

    python -m pipelines.blog_job publish <draft_id> [--private]

발행 경로:
  A·B 네이버   → publishers/naver_aside (Aside 브라우저, 캡차 없음 — 끝까지 무인)
  C·D 티스토리 → publishers/tistory_bridge (크롬 확장 브릿지 대기열. DKAPTCHA 는 텔레그램으로
                 사람에게 묻고, 발행 완료 기록·알림은 브릿지 /done 이 한다)

사람이 보지 않으므로 check_draft() 의 품질 기준을 통과하지 못한 초안은 발행하지 않는다.
가격형(B·D)은 초안 폴더의 <블로그>_products.json 으로 쿠팡 상품표를 붙인다.
이미지는 아직 올리지 않는다 — 본문만 발행.
"""
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import affiliate_notice, blog_drafts
from common.draft_writer import render_html
from common.keyword_planner import info_category
from common.logger import log
from common.publish_queue import add_url

NAVER = {"A": ("NAVER_BLOG_ID_INFO", "NAVER_CATEGORY_INFO", "NAVER_ASIDE_PROFILE_INFO"),
         "B": ("NAVER_BLOG_ID_PRICE", "NAVER_CATEGORY_PRICE", "NAVER_ASIDE_PROFILE_PRICE")}
TISTORY_ROLE = {"C": "info", "D": "price"}
PRICE_BLOGS = ("B", "D")
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
    # 가격형(B·D)은 쿠팡 상품표가 붙고, 대가성 문구가 본문 맨 위에 와야 한다 (affiliate_notice 규칙)
    prod_path = Path(item["dir"]) / f"{blog}_products.json"
    products = json.loads(prod_path.read_text(encoding="utf-8")) if prod_path.exists() else []
    if blog in PRICE_BLOGS and not products:
        return _fail(draft_id, draft["title"], "가격형 글인데 쿠팡 상품 데이터가 없음")
    body = render_html(draft, {}, blog, products)
    if products:
        body = affiliate_notice.prepend_html(body, affiliate_notice.COUPANG)
    link = products[0]["affiliate_url"] if products else ""
    source = "coupang" if products else "blog_draft"
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
    else:
        from common.tistory_blogs import resolve_blog_name
        from publishers.tistory_bridge import TistoryBridgePublisher
        pub = TistoryBridgePublisher(resolve_blog_name(TISTORY_ROLE[blog]))
        res = pub.post(draft["title"], body, tags=draft["tags"], category="", visibility=0 if private else 20,
                       source=source, keyword=item["keyword"], affiliate_url=link)
        if not res.success:
            return _fail(draft_id, draft["title"], f"티스토리 대기열 등록 실패: {res.message}")
        # 실제 발행·URL 기록·완료 알림은 확장이 끝낸 뒤 브릿지 /done 이 한다
        blog_drafts.update(draft_id, status="queued_tistory", private=private)
        log(f"[블로그발행] 티스토리 대기열 등록 {draft_id} — 확장이 발행", "ok")
        return True
    if not r.success:
        return _fail(draft_id, draft["title"], r.message)
    blog_drafts.update(draft_id, status="published", url=r.url, private=private)
    if not private:
        add_url(r.url, platform="naver", title=draft["title"], keyword=item["keyword"], source=source,
                affiliate_url=link)
    blog_drafts.notify(f"✅ [{blog}] {'비공개 ' if private else ''}발행 완료\n{draft['title']}\n{r.url}")
    log(f"[블로그발행] 발행 완료 {r.url}", "ok")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if len(sys.argv) < 3 or sys.argv[1] != "publish":
        raise SystemExit("usage: python -m pipelines.blog_job publish <draft_id> [--private]")
    ok = publish(sys.argv[2], "--private" in sys.argv)
    sys.exit(0 if ok else 1)
