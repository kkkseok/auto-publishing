"""티스토리 대기 초안 거르기 — tistory_publish._first_publishable.

목표: 품질 기준을 못 넘는 초안이 대기열 맨 앞에서 발행 시간대를 날리지 않게.
못 넘는 것은 failed 로 내리고, 통과하는 가장 오래된 초안을 고른다.
"""
import pytest

from pipelines import tistory_publish as tp


@pytest.fixture
def drafts(monkeypatch):
    store = {"old": {"status": "ready"}, "mid": {"status": "ready"}, "new": {"status": "ready"}}
    problems = {"old": ["미확인 표시가 남아 있음"], "mid": [], "new": []}
    monkeypatch.setattr(tp, "_quality_problems", lambda did: problems[did])
    monkeypatch.setattr(tp.blog_drafts, "update", lambda did, **f: store[did].update(f))
    monkeypatch.setattr(tp, "log", lambda *a, **k: None)
    return store, problems


def test_skips_failing_and_picks_next(drafts):
    store, _ = drafts
    assert tp._first_publishable(["old", "mid", "new"]) == ("mid", 1)
    assert store["old"]["status"] == "failed" and "미확인" in store["old"]["error"]
    assert store["mid"]["status"] == "ready" and store["new"]["status"] == "ready"


def test_all_failing(drafts):
    store, problems = drafts
    problems.update(mid=["본문 900자 (1500자 미만)"], new=["출처 없음"])
    assert tp._first_publishable(["old", "mid", "new"]) == ("", 3)
    assert all(v["status"] == "failed" for v in store.values())
