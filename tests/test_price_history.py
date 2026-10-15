"""가격 이력 저장·통계."""
from contextlib import closing
from datetime import date

from common import price_history as ph


def _item(pid, price, rank=1):
    return {"productId": pid, "productName": f"상품{pid}", "productPrice": price,
            "categoryName": "가전디지털", "isRocket": True, "rank": rank}


def test_record_and_stats(tmp_path):
    db = tmp_path / "p.db"
    with closing(ph.connect(db)) as conn:
        ph.record(conn, "2026-10-01", "best:1016", [_item(1, 50000)])
        ph.record(conn, "2026-10-02", "best:1016", [_item(1, 45000)])
        ph.record(conn, "2026-10-03", "best:1016", [_item(1, 48000)])
        s = ph.stats(conn, 1, days=30, today=date(2026, 10, 3))
    assert s["days_observed"] == 3
    assert s["current"] == 48000 and s["low"] == 45000 and s["high"] == 50000
    assert s["is_low"] is False
    assert s["vs_avg_pct"] == round((48000 - 47667) / 47667 * 100, 1)


def test_same_day_same_source_overwrites(tmp_path):
    db = tmp_path / "p.db"
    with closing(ph.connect(db)) as conn:
        ph.record(conn, "2026-10-01", "best:1016", [_item(1, 50000)])
        ph.record(conn, "2026-10-01", "best:1016", [_item(1, 47000)])
        n = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        s = ph.stats(conn, 1, today=date(2026, 10, 1))
    assert n == 1 and s["current"] == 47000 and s["is_low"] is True


def test_skips_items_without_price(tmp_path):
    with closing(ph.connect(tmp_path / "p.db")) as conn:
        assert ph.record(conn, "2026-10-01", "x", [{"productId": 1}, _item(2, 1000)]) == 1


def test_stats_none_when_unseen(tmp_path):
    with closing(ph.connect(tmp_path / "p.db")) as conn:
        assert ph.stats(conn, 999) is None
