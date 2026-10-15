"""매일 블로그 글을 만들어 바로 발행한다 (승인 없음 — 품질 기준은 blog_job.check_draft).

생활정보: 검색광고 API 로 고른 키워드 1개 → 조사 1회 → BLOG_DAILY_BLOGS 의 블로그마다
작성 → Aside 로 발행. 기본은 A(네이버)만 — 티스토리(C)는 발행마다 DKAPTCHA 를 사람이
풀어야 해서 완전 자동이 아니다. 'A,C' 로 넣으면 C 도 발행하고 캡차를 텔레그램으로 묻는다.
가격형 쌍(B·D)은 가격 이력이 30일 쌓이고 가격 기반 작성기가 붙은 뒤 추가한다.

이미 쓴 키워드는 data/blog_keywords_used.json 에 남겨 다시 고르지 않는다.
수동 실행: python -m pipelines.blog_drafts_daily [--keyword "에너지바우처 신청"]
스케줄: SCHEDULE_BLOG_DRAFTS
"""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts
from common.draft_writer import preview_page, research, write
from pipelines.blog_job import publish
from common.keyword_planner import plan
from common.logger import log

SCHEDULE = {
    "env":  "SCHEDULE_BLOG_DRAFTS",
    "func": "run",
}

_ROOT = Path(__file__).resolve().parent.parent
USED = _ROOT / "data" / "blog_keywords_used.json"


def _used() -> dict:
    try:
        return json.loads(USED.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mark_used(keyword: str, kind: str) -> None:
    data = _used()
    data[keyword] = {"kind": kind, "day": date.today().isoformat()}
    USED.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _pick(kind: str) -> dict | None:
    cands = plan(kind, used=set(_used()), top=10)
    return cands[0] if cands else None


def make_and_publish(keyword: str, blogs: list[str]) -> int:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "", keyword)[:20]
    out = _ROOT / "data" / "drafts" / f"{date.today():%y%m%d}_{slug}"
    out.mkdir(parents=True, exist_ok=True)
    facts_path = out / "facts.json"
    if facts_path.exists():
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
    else:
        facts = research(keyword)
        facts_path.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    published = 0
    for blog in blogs:
        draft = write(keyword, blog, facts)
        (out / f"{blog}_draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{blog}_preview.html").write_text(preview_page(draft, blog), encoding="utf-8")
        if publish(blog_drafts.register(blog, keyword, draft, out)):
            published += 1
    return published


def run(keyword: str = "") -> None:
    started = datetime.now()
    meta = ""
    if not keyword:
        pick = _pick("info")
        if not pick:
            log("[초안] 생활정보 키워드 후보 없음 (검색광고 API 확인)", "error")
            return
        keyword = pick["keyword"]
        meta = f"월 {pick['total']:,}회 · 경쟁 {pick['comp']}"
    blogs = [b.strip() for b in os.getenv("BLOG_DAILY_BLOGS", "A").split(",") if b.strip()]
    log(f"[블로그] 생활정보 {blogs} — {keyword} {meta}", "step")
    done = make_and_publish(keyword, blogs)
    _mark_used(keyword, "info")
    took = (datetime.now() - started).seconds
    if done < len(blogs):
        log(f"[블로그] 발행 {done}/{len(blogs)}건 — 실패 사유는 data/blog_drafts.json ({took}s)", "error")
    else:
        log(f"[블로그] 발행 완료 {done}건 ({took}s)", "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    kw = sys.argv[sys.argv.index("--keyword") + 1] if "--keyword" in sys.argv else ""
    run(kw)
