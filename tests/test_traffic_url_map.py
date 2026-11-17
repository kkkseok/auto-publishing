"""서치콘솔 주소(/entry/<제목>, /m/…) → 발행 기록 주소(/41) 매핑 (11-17 진단: 새 글 유입이 전부 0).

목표:
- canonical·모바일·퍼센트 인코딩 변형이 모두 같은 글로 모인다
- 같은 (글, 날짜) 의 여러 주소 행은 덮어쓰지 않고 합친다 (노출·클릭 합, 순위 노출 가중)
- 이미 저장된 /entry 행을 글 주소로 옮기고 원래 행은 지운다
"""
from urllib.parse import quote

from common import traffic_db
from pipelines import traffic_collect as tc

POST = "https://pricerader.tistory.com/41"
SLUG = "아이스박스-추천-구매-가이드"
CANON = "https://pricerader.tistory.com/entry/" + quote(SLUG)


def _conn(tmp_path):
    conn = traffic_db.connect(tmp_path / "t.db")
    traffic_db.upsert_post(conn, url=POST, blog="D", kind="price", keyword="아이스박스추천")
    traffic_db.set_canonical(conn, POST, CANON)
    return conn


def _row(page, imp, clk, pos, **keys):
    return {"keys": {"page": page, **keys}, "impressions": imp, "clicks": clk, "position": pos}


def test_variants_map_to_post(tmp_path):
    m = tc.gsc_url_map(_conn(tmp_path))
    for page in (CANON, CANON.lower().replace("%", "%"), "https://pricerader.tistory.com/m/entry/" + quote(SLUG),
                 "https://pricerader.tistory.com/entry/" + SLUG, "https://pricerader.tistory.com/m/41", POST + "/"):
        assert m.get(tc._key(page)) == POST, page


def test_merge_sums_same_post_same_day(tmp_path):
    m = tc.gsc_url_map(_conn(tmp_path))
    rows = [_row(CANON, 3, 1, 10.0, date="2026-11-10"),
            _row("https://pricerader.tistory.com/m/entry/" + quote(SLUG), 1, 0, 30.0, date="2026-11-10"),
            _row("https://pricerader.tistory.com/category/x", 2, 0, 5.0, date="2026-11-10")]
    out = tc._merge(rows, ("date",), m)
    assert out[(POST, "2026-11-10")] == {"impressions": 4, "clicks": 1, "position": 15.0}
    assert ("https://pricerader.tistory.com/category/x", "2026-11-10") in out   # 글이 아닌 주소는 그대로


def test_remap_stored_moves_entry_rows(tmp_path):
    conn = _conn(tmp_path)
    traffic_db.upsert_metric(conn, CANON, "2026-11-10", "gsc", impressions=2, clicks=0, position=20.0)
    traffic_db.upsert_metric(conn, POST, "2026-11-10", "gsc", impressions=2, clicks=1, position=10.0)
    traffic_db.upsert_query(conn, CANON, "2026-11-10", "gsc", "아이스박스", impressions=2, position=20.0)
    assert tc.remap_stored(conn, tc.gsc_url_map(conn)) == 2
    t = traffic_db.totals(conn, POST, "2026-11-01")
    assert (t["impressions"], t["clicks"], t["position"]) == (4, 1, 15.0)
    assert conn.execute("SELECT COUNT(*) FROM metrics WHERE url=?", (CANON,)).fetchone()[0] == 0
    assert traffic_db.top_queries(conn, POST, "2026-11-01")[0]["query"] == "아이스박스"
    assert tc.remap_stored(conn, tc.gsc_url_map(conn)) == 0   # 두 번째는 할 일 없음
