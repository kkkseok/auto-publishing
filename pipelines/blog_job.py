"""승인된 초안 발행 / 수정 요청 반영 — common/blog_drafts 가 답글을 받으면 분리 실행한다.

    python -m pipelines.blog_job publish <draft_id> [--private]
    python -m pipelines.blog_job revise <draft_id>

발행 경로:
  A·B 네이버 → publishers/naver_aside (Aside 브라우저)
  C·D 티스토리 → publishers/tistory_bridge (확장 브릿지. 캡차가 뜨면 텔레그램으로 답장 요청이
               오고, 완료 알림·publish_queue 기록은 브릿지의 /done 이 한다)
이미지는 아직 올리지 않는다(캡처 파일 호스팅·업로드 미구현) — 본문만 발행.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts
from common.draft_writer import preview_page, render_html, write
from common.logger import log
from common.publish_queue import add_url

NAVER = {"A": ("NAVER_BLOG_ID_INFO", "NAVER_CATEGORY_INFO", "NAVER_ASIDE_PROFILE_INFO"),
         "B": ("NAVER_BLOG_ID_PRICE", "NAVER_CATEGORY_PRICE", "NAVER_ASIDE_PROFILE_PRICE")}
TISTORY_ROLE = {"C": "info", "D": "price"}


def _load(draft_id: str) -> tuple[dict, dict, Path]:
    item = blog_drafts.get(draft_id)
    if not item:
        raise SystemExit(f"초안 없음: {draft_id}")
    d = Path(item["dir"])
    draft = json.loads((d / f"{item['blog']}_draft.json").read_text(encoding="utf-8"))
    return item, draft, d


def publish(draft_id: str, private: bool) -> None:
    item, draft, _ = _load(draft_id)
    blog = item["blog"]
    body = render_html(draft, {}, blog)
    if blog in NAVER:
        from publishers.naver_aside import publish as naver_publish
        id_env, cat_env, prof_env = NAVER[blog]
        blog_id = os.getenv(id_env, "")
        profile = os.getenv(prof_env, "")
        if blog == "B" and not profile:
            msg = "블로그 B 는 별도 Aside 프로필 필요 (NAVER_ASIDE_PROFILE_PRICE 미설정)"
            blog_drafts.update(draft_id, status="failed", error=msg)
            blog_drafts.notify(f"❌ 발행 실패: {draft['title']}\n{msg}")
            log(msg, "error")
            return
        r = naver_publish(blog_id, draft["title"], body, draft["tags"],
                          category=os.getenv(cat_env, ""), private=private, profile=profile)
        if not r.success:
            blog_drafts.update(draft_id, status="failed", error=r.message)
            blog_drafts.notify(f"❌ 네이버 발행 실패: {draft['title']}\n{r.message[:300]}")
            log(f"[블로그발행] 실패 {draft_id}: {r.message}", "error")
            return
        blog_drafts.update(draft_id, status="published", url=r.url, private=private)
        if not private:
            add_url(r.url, platform="naver", title=draft["title"], keyword=item["keyword"], source="blog_draft")
        blog_drafts.notify(f"✅ 네이버 {'비공개 ' if private else ''}발행 완료\n{draft['title']}\n{r.url}")
        log(f"[블로그발행] 완료 {r.url}", "ok")
        return
    from common.tistory_blogs import resolve_blog_name
    from publishers.tistory_bridge import TistoryBridgePublisher
    pub = TistoryBridgePublisher(resolve_blog_name(TISTORY_ROLE[blog]))
    res = pub.post(draft["title"], body, tags=draft["tags"], category="", visibility=0 if private else 20)
    if not res.success:
        blog_drafts.update(draft_id, status="failed", error=res.message)
        blog_drafts.notify(f"❌ 티스토리 대기열 등록 실패: {draft['title']}\n{res.message[:300]}")
        log(f"[블로그발행] 티스토리 큐 실패 {draft_id}: {res.message}", "error")
        return
    # 실제 발행 완료·URL 기록·텔레그램 완료 알림은 브릿지 /done 이 한다
    blog_drafts.update(draft_id, status="queued_tistory", private=private)
    log(f"[블로그발행] 티스토리 대기열 등록 {draft_id}", "ok")


def revise(draft_id: str) -> None:
    item, draft, d = _load(draft_id)
    blog = item["blog"]
    facts = json.loads((d / "facts.json").read_text(encoding="utf-8"))
    new = write(item["keyword"], blog, facts, feedback=item.get("reply", ""), previous=draft)
    n = sum(1 for p in d.glob(f"{blog}_draft*.json"))
    (d / f"{blog}_draft.v{n}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / f"{blog}_draft.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    prev = d / f"{blog}_preview.html"
    prev.write_text(preview_page(new, blog), encoding="utf-8")
    blog_drafts.update(draft_id, status="superseded")
    blog_drafts.request(blog, item["keyword"], new, d, prev, meta=f"(수정 {n}회차)")
    log(f"[블로그발행] 수정본 재요청 {draft_id}", "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    cmd, did = sys.argv[1], sys.argv[2]
    try:
        if cmd == "publish":
            publish(did, "--private" in sys.argv)
        elif cmd == "revise":
            revise(did)
    except Exception as e:
        blog_drafts.update(did, status="failed", error=str(e)[:300])
        blog_drafts.notify(f"❌ 작업 실패 ({cmd}) {did}: {e}")
        raise
