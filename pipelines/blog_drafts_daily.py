"""매일 블로그 글을 만들어 바로 발행한다 (승인 없음 — 품질 기준은 blog_job.check_draft).

생활정보: 검색광고 API 로 고른 키워드 1개 → 조사 1회 → BLOG_DAILY_BLOGS 의 블로그마다
작성. 네이버(A)는 바로 Aside 로 발행하고, 티스토리(C)는 발행마다 DKAPTCHA 를 사람이
답해야 해서 대기(ready)로 두고 pipelines.tistory_publish 가 SCHEDULE_TISTORY_PUBLISH 에
발행한다 — 사용자가 깨어 있는 시간에 캡차가 오도록.
가격형: 같은 방식으로 가격 키워드 1개 → 쿠팡 검색 상위 상품 + 구매 기준 조사 →
BLOG_DAILY_PRICE_BLOGS 의 블로그마다 작성(common/price_writer). 상품표·링크는 코드가 넣는다.

이미 쓴 키워드는 data/blog_keywords_used.json 에 남겨 다시 고르지 않는다.
수동 실행: python -m pipelines.blog_drafts_daily [--keyword "에너지바우처 신청"] [--price "가습기 추천"]
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

from common import blog_drafts, price_writer
from common.draft_writer import preview_page, research, write
from pipelines.blog_job import NAVER, publish
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
        published += _publish_or_queue(blog, keyword, draft, out)
    return published


def _publish_or_queue(blog: str, keyword: str, draft: dict, out: Path) -> bool:
    if blog not in NAVER:
        blog_drafts.register(blog, keyword, draft, out, status="ready")
        log(f"[블로그] [{blog}] 발행 대기 등록 — {draft['title']}", "ok")
        return True
    return publish(blog_drafts.register(blog, keyword, draft, out))


def make_price_posts(keyword: str, blogs: list[str]) -> int:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "", keyword)[:20]
    out = _ROOT / "data" / "drafts" / f"{date.today():%y%m%d}_{slug}"
    out.mkdir(parents=True, exist_ok=True)
    products = price_writer.fetch_products(keyword)
    if len(products) < 3:
        log(f"[블로그] 쿠팡 상품 {len(products)}개 — 가격형 글을 쓰기에 부족 ({keyword})", "error")
        return 0
    facts = price_writer.facts(keyword, products)
    (out / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    done = 0
    for blog in blogs:
        prods = price_writer.for_blog(products, blog)
        draft = write(keyword, blog, facts)
        (out / f"{blog}_draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{blog}_products.json").write_text(json.dumps(prods, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{blog}_preview.html").write_text(preview_page(draft, blog, products=prods), encoding="utf-8")
        done += _publish_or_queue(blog, keyword, draft, out)
    return done


def _blogs(env: str, default: str = "") -> list[str]:
    return [b.strip() for b in os.getenv(env, default).split(",") if b.strip()]


def run_price(keyword: str = "", blogs: list[str] | None = None) -> tuple[int, int]:
    blogs = _blogs("BLOG_DAILY_PRICE_BLOGS") if blogs is None else blogs
    if not blogs:
        return 0, 0
    if not keyword:
        pick = _pick("price")
        if not pick:
            log("[블로그] 가격형 키워드 후보 없음 (검색광고 API 확인)", "error")
            return 0, len(blogs)
        keyword = pick["keyword"]
    log(f"[블로그] 가격형 {blogs} — {keyword}", "step")
    done = make_price_posts(keyword, blogs)
    _mark_used(keyword, "price")
    return done, len(blogs)


def run_info(keyword: str = "", blogs: list[str] | None = None) -> tuple[int, int]:
    blogs = _blogs("BLOG_DAILY_BLOGS", "A") if blogs is None else blogs
    if not blogs:
        return 0, 0
    meta = ""
    if not keyword:
        pick = _pick("info")
        if not pick:
            log("[블로그] 생활정보 키워드 후보 없음 (검색광고 API 확인)", "error")
            return 0, len(blogs)
        keyword = pick["keyword"]
        meta = f"월 {pick['total']:,}회 · 경쟁 {pick['comp']}"
    log(f"[블로그] 생활정보 {blogs} — {keyword} {meta}", "step")
    done = make_and_publish(keyword, blogs)
    _mark_used(keyword, "info")
    return done, len(blogs)


def run(info_keyword: str = "", price_keyword: str = "", only: str = "",
        info_blogs: list[str] | None = None, price_blogs: list[str] | None = None) -> None:
    started = datetime.now()
    done = want = 0
    for kind, fn, kw, blogs in (("info", run_info, info_keyword, info_blogs),
                                 ("price", run_price, price_keyword, price_blogs)):
        if only and only != kind:
            continue
        try:
            d, w = fn(kw, blogs)
        except Exception as e:   # 한쪽이 죽어도 다른 쪽은 돌린다 — 실패는 error 로 남긴다
            log(f"[블로그] {kind} 실패: {e}", "error")
            d, w = 0, 1
        done, want = done + d, want + w
    took = (datetime.now() - started).seconds
    if done < want:
        log(f"[블로그] 발행·대기등록 {done}/{want}건 — 실패 사유는 로그·data/blog_drafts.json ({took}s)", "error")
    else:
        log(f"[블로그] 발행 완료 (대기 등록 포함) {done}건 ({took}s)", "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else ""
    # --keyword: 생활정보만, --price: 가격형만, 둘 다 없으면 일일 실행과 같다
    run(arg("--keyword"), arg("--price"),
        only="info" if "--keyword" in sys.argv and "--price" not in sys.argv
        else "price" if "--price" in sys.argv and "--keyword" not in sys.argv else "")
