"""쿠팡 상품 가격 이력 저장소 (SQLite, data/price_history.db).

가격형 블로그(B: 네이버 taq87641, D: 티스토리 pricerader)의 고유 데이터다.
pipelines/price_collector.py 가 매일 베스트셀러를 적재하고, 글 생성기는 여기서
상품별 30일 최저가·평균·변동을 읽는다. 쿠팡 API 에는 상품 ID 로 가격을 조회하는
기능이 없어서, 베스트셀러에 반복해서 잡히는 인기 상품 위주로 이력이 쌓인다.
"""
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "price_history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    category_name TEXT,
    image         TEXT,
    product_url   TEXT,
    is_rocket     INTEGER,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    product_id INTEGER NOT NULL,
    day        TEXT NOT NULL,
    source     TEXT NOT NULL,
    price      INTEGER NOT NULL,
    rank       INTEGER,
    PRIMARY KEY (product_id, day, source)
);
CREATE INDEX IF NOT EXISTS idx_obs_day ON observations(day);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def record(conn: sqlite3.Connection, day: str, source: str, items: list[dict]) -> int:
    """API item 목록을 하루치 관측으로 저장. 같은 날·같은 출처는 덮어쓴다. 저장 건수 반환."""
    n = 0
    for it in items:
        pid = it.get("productId")
        price = it.get("productPrice")
        if not pid or price in (None, ""):
            continue
        conn.execute(
            """INSERT INTO products (product_id, name, category_name, image, product_url,
                                     is_rocket, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id) DO UPDATE SET
                 name=excluded.name, category_name=excluded.category_name,
                 image=excluded.image, product_url=excluded.product_url,
                 is_rocket=excluded.is_rocket, last_seen=excluded.last_seen""",
            (int(pid), it.get("productName", ""), it.get("categoryName", ""),
             it.get("productImage", ""), it.get("productUrl", ""),
             1 if it.get("isRocket") else 0, day, day))
        conn.execute(
            """INSERT OR REPLACE INTO observations (product_id, day, source, price, rank)
               VALUES (?, ?, ?, ?, ?)""",
            (int(pid), day, source, int(float(price)), it.get("rank")))
        n += 1
    conn.commit()
    return n


def stats(conn: sqlite3.Connection, product_id: int, days: int = 30,
          today: date | None = None) -> dict | None:
    """최근 days 일 가격 통계 — 날짜별 최저 관측가 기준. 관측이 없으면 None."""
    today = today or date.today()
    since = (today - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        """SELECT day, MIN(price) FROM observations
           WHERE product_id = ? AND day >= ? GROUP BY day ORDER BY day""",
        (product_id, since)).fetchall()
    if not rows:
        return None
    prices = [p for _, p in rows]
    current = prices[-1]
    avg = sum(prices) / len(prices)
    low = min(prices)
    return {
        "days_observed": len(rows),
        "current": current,
        "current_day": rows[-1][0],
        "low": low,
        "high": max(prices),
        "avg": round(avg),
        "vs_avg_pct": round((current - avg) / avg * 100, 1) if avg else 0.0,
        "is_low": current <= low,
        "series": rows,
    }


def summary(path: Path = DB_PATH) -> dict:
    with closing(connect(path)) as conn:
        products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        days = conn.execute("SELECT COUNT(DISTINCT day) FROM observations").fetchone()[0]
        obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    return {"products": products, "days": days, "observations": obs}
