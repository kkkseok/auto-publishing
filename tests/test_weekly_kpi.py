"""주간 KPI 집계 로직."""
from datetime import date

import pipelines.weekly_kpi as kpi

DAILY = {
    "2026-10-06": {"a": {"clicks": 5, "orders": 0, "commission": 0, "gmv": 0, "platforms": ["tistory"]}},
    "2026-10-13": {
        "aptsx1": {"clicks": 3, "orders": 1, "commission": 900, "gmv": 30000,
                   "platforms": ["tistory"], "post_url": "https://b/1"},
        "apnbx2": {"clicks": 2, "orders": 0, "commission": 0, "gmv": 0,
                   "platforms": ["naver"], "post_url": "https://n/2"},
    },
}


def test_totals_only_count_window():
    start, end = kpi._window(date(2026, 10, 14))
    t = kpi._coupang_totals(DAILY, start, end)
    assert t["clicks"] == 5 and t["commission"] == 900


def test_by_platform_and_top_posts():
    start, end = kpi._window(date(2026, 10, 14))
    plat = kpi._coupang_by_platform(DAILY, start, end)
    assert plat["tistory"]["commission"] == 900
    top = kpi._top_posts(DAILY, start, end)
    assert top[0][0] == "https://b/1"


def test_pct():
    assert kpi._pct(0, 0) == "전주 0"
    assert kpi._pct(15, 10) == "+50%"
