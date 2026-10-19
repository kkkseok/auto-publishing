"""기존 글 수정안 만들기 — 유입 데이터로 고를 만한 글을 다시 써서 텔레그램 승인을 받는다.

후보는 common/content_insights 가 고른다 (revision_candidates):
  low_ctr   구글 노출은 많은데 클릭이 적다 → 제목·설명·도입부를 실제 검색어에 맞게
  striking  평균 8~20위 → 다시 조사해서 검색어가 묻는데 본문이 답하지 않는 것을 보강

수정안은 새 글과 같은 품질 기준(pipelines.blog_job.check_draft)을 통과해야 보낸다.
초안 폴더에 <블로그>_draft_rev<n>.json·_preview_rev<n>.html 로 남기고, 원본은 그대로 둔다.
승인된 수정안을 실제 글에 반영하는 단계는 아직 없다 — 승인 기록(data/revisions.json)만 남는다.

수동 실행: python -m pipelines.content_revise [--dry-run]
스케줄: SCHEDULE_CONTENT_REVISE  ·  1회 최대 REVISE_PER_RUN 개 (기본 1)
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts, content_insights, revisions
from common.logger import log

SCHEDULE = {
    "env":  "SCHEDULE_CONTENT_REVISE",
    "func": "run",
}

REASON_LABEL = {"low_ctr": "노출 대비 클릭 적음", "striking": "2페이지 순위(8~20위)"}


def _post(url: str) -> dict:
    return next((p for p in content_insights.load().get("posts", []) if p["url"] == url), {})


def instructions(reason: str, post: dict) -> str:
    """write(feedback=...) 에 넘길 수정 지시 — 실제 수치와 검색어를 넣는다."""
    qs = ", ".join(q["query"] for q in post.get("top_queries", [])[:10]) or "(검색어 데이터 없음)"
    ctr = f"{post['ctr'] * 100:.1f}%" if post.get("ctr") is not None else "-"
    stats = (f"최근 {content_insights.WINDOW_DAYS}일 구글 노출 {post.get('impressions', 0)}회, "
             f"클릭 {post.get('clicks', 0)}회(CTR {ctr}), 평균 {post.get('position') or '-'}위.\n"
             f"이 글로 실제 들어온 검색어: {qs}")
    if reason == "low_ctr":
        return (stats + "\n검색 결과에는 보이는데 클릭이 적다. title·meta_description·intro 를 위 검색어를 쓴 "
                "사람이 '내 질문의 답이 여기 있다'고 느끼게 다시 써라 — 검색어의 핵심 단어를 제목 앞쪽에, "
                "구체적인 기준(연도·금액·대상)을 제목에. 과장·낚시 표현은 쓰지 않는다. "
                "sections·faq 는 사실이 바뀌지 않았으면 그대로 둔다.")
    return (stats + "\n검색 결과 2페이지에 있다. 위 검색어 가운데 지금 본문이 답하지 않는 질문을 찾아 "
            "소제목이나 FAQ 로 보강하고, 사실 시트가 바뀐 수치는 갱신해라. 제목의 핵심 키워드는 유지한다. "
            "글을 늘리려고 같은 내용을 반복하지 않는다.")


def propose(cand: dict, dry_run: bool = False) -> bool:
    from common.draft_writer import preview_page, research, write
    from pipelines.blog_job import check_draft
    item = blog_drafts.get(cand["draft_id"]) or {}
    folder = Path(item.get("dir") or "")
    blog = cand["blog"]
    src = folder / f"{blog}_draft.json"
    if not src.exists():
        log(f"[수정안] 원본 초안 없음 — {cand['url']} ({src})", "error")
        return False
    previous = json.loads(src.read_text(encoding="utf-8"))
    post = _post(cand["url"])
    keyword = item.get("keyword") or post.get("keyword", "")
    if cand["reason"] == "striking" or not (folder / "facts.json").exists():
        facts = research(keyword)   # 보강은 최신 사실로 — 원본 조사가 몇 주 전이다
    else:
        facts = json.loads((folder / "facts.json").read_text(encoding="utf-8"))
    guide = instructions(cand["reason"], post)
    if dry_run:
        print(f"[dry-run] {cand['url']}\n{guide}")
        return True
    draft = write(keyword, blog, facts, feedback=guide, previous=previous)
    problems = check_draft(draft)
    n = 1 + sum(1 for _ in folder.glob(f"{blog}_draft_rev*.json"))
    out = folder / f"{blog}_draft_rev{n}.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    products = folder / f"{blog}_products.json"
    prods = json.loads(products.read_text(encoding="utf-8")) if products.exists() else None
    (folder / f"{blog}_preview_rev{n}.html").write_text(preview_page(draft, blog, products=prods), encoding="utf-8")
    if problems:
        log(f"[수정안] 품질 기준 미달로 보내지 않음 — {cand['url']}: {', '.join(problems)}", "error")
        return False
    rid = revisions.add(url=cand["url"], draft_id=cand["draft_id"], blog=blog, reason=cand["reason"],
                        old_title=previous["title"], new_title=draft["title"], draft_path=str(out),
                        stats={k: post.get(k) for k in ("impressions", "clicks", "ctr", "position")})
    added = [s["heading"] for s in draft.get("sections", [])
             if s["heading"] not in {x["heading"] for x in previous.get("sections", [])}]
    text = (f"✏️ 기존 글 수정 제안 [{blog}] — {REASON_LABEL.get(cand['reason'], cand['reason'])}\n"
            f"{cand['url']}\n\n{guide.splitlines()[0]}\n\n"
            f"제목\n  전: {previous['title']}\n  후: {draft['title']}\n"
            + (f"새 소제목: {', '.join(added[:5])}\n" if added else "")
            + f"\n미리보기: {folder / f'{blog}_preview_rev{n}.html'}\n\n"
            "이 메시지에 '승인'이라고 답장하면 반영 대기로 넘어갑니다. 다른 답장은 보류.")
    if not revisions.send_for_approval(rid, text):
        revisions.update(rid, status="send_failed")
        return False
    log(f"[수정안] 승인 요청 보냄 {rid} — {cand['url']}", "ok")
    return True


def run(dry_run: bool = False) -> None:
    limit = int(os.getenv("REVISE_PER_RUN", "1") or 1)
    waiting = revisions.pending_urls()
    cands = [c for c in content_insights.load().get("revision_candidates", []) if c["url"] not in waiting]
    if not cands:
        log("[수정안] 수정 후보 없음 (새 글은 14일 뒤부터 판단)", "info")
        return
    ok = fail = 0
    for cand in cands[:limit]:
        try:
            done = propose(cand, dry_run)
        except Exception as e:
            log(f"[수정안] 실패 {cand['url']}: {e}", "error")
            done = False
        ok, fail = ok + done, fail + (not done)
    log(f"[수정안] 승인 요청 {ok}건" + (f" · 실패 {fail}건" if fail else ""), "error" if fail else "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run(dry_run="--dry-run" in sys.argv)
