"""content_insights 유입 분석 테스트 — 글 상태 분류, 주제 가중치, 작성 힌트.

목표: 유입 데이터가 다음 글 선정·작성에 엉뚱하게 반영되지 않게.
- 새 글(14일 미만)은 판단 보류, 21일 넘게 노출 0 이면 dead
- 노출 많고 CTR 낮으면 low_ctr, 8~20위면 striking → 수정 후보
- 주제 가중치는 0.5~2.0 안, 데이터 없으면 1.0
- 네이버 블로그 단위 검색어 중 지금 주제와 무관한 것은 새 글 후보에서 빠짐
"""
from datetime import date, timedelta

import pytest

from common import content_insights, traffic_db

TODAY = date(2026, 12, 1)


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(traffic_db, "PATH", tmp_path / "traffic.db")
    monkeypatch.setattr(content_insights, "PATH", tmp_path / "insights.json")
    monkeypatch.setattr(content_insights, "ROI", tmp_path / "roi.json")
    monkeypatch.setattr(content_insights, "REVISIONS", tmp_path / "revisions.json")
    content_insights._CACHE.clear()
    conn = traffic_db.connect()
    yield conn
    conn.close()
    content_insights._CACHE.clear()


def _post(conn, url, blog, keyword, seed, age, category="지원금·복지", kind="info"):
    traffic_db.upsert_post(conn, url=url, blog=blog, kind=kind, keyword=keyword, seed=seed, category=category,
                           title=f"{keyword} 정리", published=_day(age), draft_id=url[-4:], draft_dir="", log_no="")


def _gsc(conn, url, days, imp, clk, pos):
    for i in range(days):
        traffic_db.upsert_metric(conn, url, _day(i + 1), "gsc", impressions=imp, clicks=clk, position=pos)


def _by_url(data):
    return {p["url"]: p for p in data["posts"]}


def test_new_and_dead(env):
    _post(env, "https://c.tistory.com/1", "C", "근로장려금 대상", "근로장려금", age=5)
    _post(env, "https://c.tistory.com/2", "C", "에너지바우처 신청", "에너지바우처", age=25)
    env.commit()
    content_insights.build(TODAY)
    posts = _by_url(content_insights.load())
    assert posts["https://c.tistory.com/1"]["status"] == "new"
    assert posts["https://c.tistory.com/2"]["status"] == "dead"


def test_low_ctr_and_striking_become_candidates(env):
    _post(env, "https://c.tistory.com/1", "C", "근로장려금 대상", "근로장려금", age=30)
    _post(env, "https://c.tistory.com/2", "C", "실업급여 신청", "실업급여", age=30, category="취업·청년")
    _gsc(env, "https://c.tistory.com/1", 10, imp=20, clk=0, pos=4.0)     # 노출 200, 클릭 0
    _gsc(env, "https://c.tistory.com/2", 10, imp=5, clk=0, pos=12.0)     # 노출 50, 12위
    env.commit()
    content_insights.build(TODAY)
    data = content_insights.load()
    posts = _by_url(data)
    assert posts["https://c.tistory.com/1"]["status"] == "low_ctr"
    assert posts["https://c.tistory.com/2"]["status"] == "striking"
    assert {c["reason"] for c in data["revision_candidates"]} == {"low_ctr", "striking"}


def test_recently_revised_post_is_not_candidate_again(env):
    import json
    _post(env, "https://c.tistory.com/1", "C", "근로장려금 대상", "근로장려금", age=30)
    _gsc(env, "https://c.tistory.com/1", 10, imp=20, clk=0, pos=4.0)
    env.commit()
    content_insights.REVISIONS.write_text(json.dumps(
        {"r1": {"url": "https://c.tistory.com/1", "created": _day(3)}}), encoding="utf-8")
    content_insights.build(TODAY)
    assert content_insights.load()["revision_candidates"] == []


def test_topic_multiplier_rewards_read_topics(env):
    _post(env, "https://c.tistory.com/1", "C", "근로장려금 대상", "근로장려금", age=30)
    _post(env, "https://c.tistory.com/2", "C", "에너지바우처 신청", "에너지바우처", age=30)
    _gsc(env, "https://c.tistory.com/1", 20, imp=10, clk=3, pos=5.0)
    env.commit()
    content_insights.build(TODAY)
    up = content_insights.topic_multiplier("info", "근로장려금", "")
    down = content_insights.topic_multiplier("info", "에너지바우처", "")
    assert up > 1.0 > down
    assert 0.5 <= down and up <= 2.0
    assert content_insights.topic_multiplier("info", "없는씨앗", "") == 1.0


def test_no_data_means_neutral(env):
    content_insights.build(TODAY)
    assert content_insights.topic_multiplier("info", "근로장려금", "지원금·복지") == 1.0
    assert content_insights.opportunity_bonus("info", "근로장려금 신청") == 1.0
    assert content_insights.writer_hints("C", "근로장려금 신청", "근로장려금", "지원금·복지") == ""


def test_opportunities_skip_unrelated_blog_queries(env):
    _post(env, "https://blog.naver.com/a/224000000001", "A", "근로장려금 대상", "근로장려금", age=20)
    traffic_db.upsert_query(env, "naver:A", _day(2), "naver_stat", "근로장려금 신청기간", views=3)
    traffic_db.upsert_query(env, "naver:A", _day(2), "naver_stat", "마운틴하이커 자충매트", views=5)
    env.commit()
    content_insights.build(TODAY)
    queries = [o["query"] for o in content_insights.load()["opportunities"]["info"]]
    assert queries == ["근로장려금 신청기간"]
    assert content_insights.opportunity_bonus("info", "근로장려금 신청기간") == 1.3


def test_writer_hints_include_real_queries(env):
    _post(env, "https://c.tistory.com/1", "C", "근로장려금 대상", "근로장려금", age=30)
    _gsc(env, "https://c.tistory.com/1", 10, imp=10, clk=2, pos=6.0)
    traffic_db.upsert_query(env, "https://c.tistory.com/1", _day(2), "gsc", "근로장려금 재산 기준",
                            impressions=30, clicks=2, position=6.0)
    env.commit()
    content_insights.build(TODAY)
    hints = content_insights.writer_hints("C", "근로장려금 신청", "근로장려금", "지원금·복지")
    assert "근로장려금 재산 기준" in hints
    assert "근로장려금 대상 정리" in hints
