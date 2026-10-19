"""유입 데이터 저장소 — data/traffic.db (SQLite).

발행만 하고 유입을 보지 않으면 840건 실험처럼 아무도 찾지 않는 글을 계속 쓰게 된다.
그래서 글마다 검색 노출·클릭·조회수를 매일 모아 두고, common/content_insights 가
이걸 읽어 다음 키워드 선정과 기존 글 수정에 반영한다.

테이블:
  posts    — 우리가 발행한 글 (blog_drafts.json 에서 동기화). url 이 기준 키.
  metrics  — 글·날짜·출처별 수치. source: gsc(구글) | naver_stat(네이버 블로그 통계)
             gsc 는 impressions·clicks·position, naver_stat 는 views 만 채운다.
  queries  — 검색어별 수치. gsc 는 글 단위, 네이버 통계는 블로그 단위라 url 에
             'naver:<블로그코드>' 를 넣는다.

같은 (url, day, source[, query]) 를 다시 넣으면 덮어쓴다 — 구글은 2~3일 늦게 확정되므로
수집기는 최근 며칠을 매번 다시 받아 덮는다.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "traffic.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    url TEXT PRIMARY KEY, blog TEXT, kind TEXT, keyword TEXT, seed TEXT, category TEXT,
    title TEXT, published TEXT, draft_id TEXT, draft_dir TEXT, log_no TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    url TEXT, day TEXT, source TEXT,
    impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, views INTEGER DEFAULT 0,
    position REAL,
    PRIMARY KEY (url, day, source)
);
CREATE TABLE IF NOT EXISTS queries (
    url TEXT, day TEXT, source TEXT, query TEXT,
    impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0, views INTEGER DEFAULT 0,
    position REAL,
    PRIMARY KEY (url, day, source, query)
);
CREATE INDEX IF NOT EXISTS metrics_day ON metrics(day);
CREATE INDEX IF NOT EXISTS queries_day ON queries(day);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert_post(conn: sqlite3.Connection, **p) -> None:
    conn.execute(
        "INSERT INTO posts (url, blog, kind, keyword, seed, category, title, published, draft_id, draft_dir, log_no) "
        "VALUES (:url, :blog, :kind, :keyword, :seed, :category, :title, :published, :draft_id, :draft_dir, :log_no) "
        "ON CONFLICT(url) DO UPDATE SET blog=excluded.blog, kind=excluded.kind, keyword=excluded.keyword, "
        "seed=excluded.seed, category=excluded.category, title=excluded.title, published=excluded.published, "
        "draft_id=excluded.draft_id, draft_dir=excluded.draft_dir, log_no=excluded.log_no",
        {k: p.get(k, "") for k in ("url", "blog", "kind", "keyword", "seed", "category", "title",
                                    "published", "draft_id", "draft_dir", "log_no")})


def upsert_metric(conn: sqlite3.Connection, url: str, day: str, source: str, *,
                  impressions: int = 0, clicks: int = 0, views: int = 0, position: float | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO metrics (url, day, source, impressions, clicks, views, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (url, day, source, impressions, clicks, views, position))


def upsert_query(conn: sqlite3.Connection, url: str, day: str, source: str, query: str, *,
                 impressions: int = 0, clicks: int = 0, views: int = 0, position: float | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO queries (url, day, source, query, impressions, clicks, views, position) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (url, day, source, query, impressions, clicks, views, position))


def posts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM posts")]


def totals(conn: sqlite3.Connection, url: str, since: str) -> dict:
    """since(포함) 이후 합계 — 노출·클릭·조회수와 노출 가중 평균 순위."""
    r = conn.execute(
        "SELECT COALESCE(SUM(impressions),0) imp, COALESCE(SUM(clicks),0) clk, COALESCE(SUM(views),0) views, "
        "SUM(position*impressions)/NULLIF(SUM(CASE WHEN position IS NOT NULL THEN impressions END),0) pos "
        "FROM metrics WHERE url=? AND day>=?", (url, since)).fetchone()
    return {"impressions": r["imp"], "clicks": r["clk"], "views": r["views"],
            "position": round(r["pos"], 1) if r["pos"] is not None else None}


def top_queries(conn: sqlite3.Connection, url: str, since: str, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT query, SUM(impressions) imp, SUM(clicks) clk, SUM(views) views, "
        "SUM(position*impressions)/NULLIF(SUM(impressions),0) pos FROM queries "
        "WHERE url=? AND day>=? GROUP BY query ORDER BY imp+views DESC, clk DESC LIMIT ?",
        (url, since, limit)).fetchall()
    return [{"query": r["query"], "impressions": r["imp"], "clicks": r["clk"], "views": r["views"],
             "position": round(r["pos"], 1) if r["pos"] is not None else None} for r in rows]


def with_conn(fn):
    """짧은 작업용 — with closing(connect()) as conn: fn(conn); commit."""
    with closing(connect()) as conn:
        out = fn(conn)
        conn.commit()
        return out
