"""매일 유입 데이터 수집 → 분석 — 발행한 글이 실제로 읽히는지 본다.

  1. data/blog_drafts.json 의 발행 글을 data/traffic.db posts 로 동기화 (키워드·씨앗·카테고리)
  2. 구글 서치콘솔: 티스토리 C·D 의 글·검색어별 노출·클릭·순위 (최근 GSC_DAYS 일 덮어쓰기)
  3. 네이버 블로그 통계: A·B 의 글별 조회수·유입 검색어 (Aside 로그인 세션, 최근 NAVER_STAT_DAYS 일)
  4. common/content_insights.build() — 글 상태 분류·주제별 성과 → data/content_insights.json
     (다음 키워드 선정·글 작성과 기존 글 수정 후보가 이 파일을 읽는다)

한 출처가 실패해도 나머지는 모으고, 실패는 error 로 남겨 ledger 가 failure 로 기록한다.
처음 실행이면 각 출처를 BACKFILL_DAYS 일 거슬러 채운다.

수동 실행: python -m pipelines.traffic_collect [--days 28]
스케줄: SCHEDULE_TRAFFIC_COLLECT
"""
import json
import os
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import traffic_db
from common.keyword_planner import category_for, seed_of
from common.logger import log

SCHEDULE = {
    "env":  "SCHEDULE_TRAFFIC_COLLECT",
    "func": "run",
}

_ROOT = Path(__file__).resolve().parent.parent
DRAFTS = _ROOT / "data" / "blog_drafts.json"
NAVER_BLOGS = {"A": ("NAVER_BLOG_ID_INFO", "NAVER_ASIDE_ACCOUNT_INFO"),
               "B": ("NAVER_BLOG_ID_PRICE", "NAVER_ASIDE_ACCOUNT_PRICE")}
TISTORY_BLOGS = {"C": "TISTORY_BLOG_INFO", "D": "TISTORY_BLOG_PRICE"}
PRICE_BLOGS = ("B", "D")
BACKFILL_DAYS = 28


def _days(n: int, lag: int = 1) -> list[str]:
    """어제(lag=1)부터 n 일 — 최근 날짜 먼저."""
    return [(date.today() - timedelta(days=lag + i)).isoformat() for i in range(n)]


def sync_posts(conn) -> int:
    from sources.traffic_naver import log_no
    try:
        drafts = json.loads(DRAFTS.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[유입] blog_drafts.json 읽기 실패: {e}", "error")
        return 0
    n = 0
    for did, d in drafts.items():
        if d.get("status") != "published" or not d.get("url") or d.get("private"):
            continue
        kind = "price" if d["blog"] in PRICE_BLOGS else "info"
        traffic_db.upsert_post(conn, url=d["url"], blog=d["blog"], kind=kind, keyword=d.get("keyword", ""),
                               seed=seed_of(d.get("keyword", ""), kind),
                               category=category_for(d["blog"], d.get("keyword", "")),
                               title=d.get("title", ""), published=(d.get("updated") or d.get("created", ""))[:10],
                               draft_id=did, draft_dir=d.get("dir", ""), log_no=log_no(d["url"]))
        n += 1
    return n


def _has(conn, source: str, like: str) -> bool:
    return conn.execute("SELECT 1 FROM metrics WHERE source=? AND url LIKE ? LIMIT 1", (source, like)).fetchone() is not None


def collect_gsc(conn, days: int | None) -> int:
    from sources import traffic_gsc
    headers = traffic_gsc._headers()
    have = set(traffic_gsc.sites(headers))
    rows_saved = 0
    for blog, env in TISTORY_BLOGS.items():
        name = os.getenv(env, "").strip()
        if not name:
            continue
        site = f"https://{name}.tistory.com/"
        if site not in have:
            log(f"[유입] 서치콘솔에 {site} 없음 — 소유확인 필요", "error")
            continue
        n = days or (int(os.getenv("GSC_DAYS", "7")) if _has(conn, "gsc", site + "%") else BACKFILL_DAYS)
        # 서치콘솔은 2~3일 늦다 — 어제까지 받되 최근값은 다음 실행에서 다시 덮는다
        start, end = _days(n)[-1], _days(1)[0]
        for r in traffic_gsc.query(site, start, end, ["date", "page"], headers):
            k = r["keys"]
            traffic_db.upsert_metric(conn, k["page"], k["date"], "gsc", impressions=r["impressions"],
                                     clicks=r["clicks"], position=r["position"])
            rows_saved += 1
        for r in traffic_gsc.query(site, start, end, ["date", "page", "query"], headers):
            k = r["keys"]
            traffic_db.upsert_query(conn, k["page"], k["date"], "gsc", k["query"], impressions=r["impressions"],
                                    clicks=r["clicks"], position=r["position"])
        log(f"[유입] 서치콘솔 {name}: {start}~{end}", "info")
    return rows_saved


def collect_naver(conn, days: int | None) -> tuple[int, list[str]]:
    from sources.traffic_naver import NaverStatError, fetch_days
    saved, errors = 0, []
    for blog, (id_env, acct_env) in NAVER_BLOGS.items():
        blog_id = os.getenv(id_env, "").strip()
        if not blog_id:
            continue
        by_log = {p["log_no"]: p["url"] for p in traffic_db.posts(conn) if p["blog"] == blog and p["log_no"]}
        first = not _has(conn, "naver_stat", f"https://blog.naver.com/{blog_id}/%") and not any(
            _has(conn, "naver_stat", u) for u in by_log.values())
        n = days or (BACKFILL_DAYS if first else int(os.getenv("NAVER_STAT_DAYS", "3")))
        try:
            got = fetch_days(blog_id, _days(n), account=os.getenv(acct_env, ""))
        except NaverStatError as e:
            errors.append(f"네이버 {blog}({blog_id}): {e}")
            continue
        for day, d in got.items():
            for p in d["posts"]:
                url = by_log.get(p["log_no"]) or f"https://blog.naver.com/{blog_id}/{p['log_no']}"
                traffic_db.upsert_metric(conn, url, day, "naver_stat", views=p["views"])
                saved += 1
            for q in d["queries"]:
                traffic_db.upsert_query(conn, f"naver:{blog}", day, "naver_stat", q["query"], views=q["views"])
        log(f"[유입] 네이버 통계 {blog}({blog_id}): {len(got)}일", "info")
    return saved, errors


def run(days: int | None = None) -> None:
    errors = []
    with closing(traffic_db.connect()) as conn:
        n_posts = sync_posts(conn)
        conn.commit()
        try:
            gsc = collect_gsc(conn, days)
            conn.commit()
        except Exception as e:
            gsc = 0
            errors.append(f"서치콘솔: {e}")
        naver, nerr = collect_naver(conn, days)
        conn.commit()
        errors += nerr
    from common import content_insights
    try:
        summary = content_insights.build()
    except Exception as e:
        summary = ""
        errors.append(f"분석: {e}")
    for e in errors:
        log(f"[유입] 수집 실패 — {e}", "error")
    log(f"[유입] 글 {n_posts}개 · 서치콘솔 {gsc}행 · 네이버 통계 {naver}행"
        + (f" · {summary}" if summary else ""), "error" if errors else "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run(int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else None)
