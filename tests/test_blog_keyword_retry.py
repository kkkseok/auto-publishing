"""미확인 키워드 건너뛰기 테스트 — blog_drafts_daily._with_retries.

목표: 공식 수치를 확보하지 못한 키워드(초안에 '확인 필요')는 발행 전에 버리고 다음 후보로.
- 버린 키워드는 skipped 로 기록 (같은 씨앗 30일 쉼)
- 수동 키워드는 다시 고르지 않음
- 전부 막히면 error 로그 (ledger 가 failure 로 기록)
"""
import pytest

from pipelines import blog_drafts_daily as bd


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "USED", tmp_path / "used.json")
    picks = iter([{"keyword": k, "total": 1000, "comp": "낮음"} for k in ("증여세 계산", "재산세 계산", "실업급여 신청")])
    monkeypatch.setattr(bd, "_pick", lambda kind: next(picks, None))
    logs = []
    monkeypatch.setattr(bd, "log", lambda msg, level="info": logs.append((level, msg)))
    return logs


def _make(blocked):
    calls = []

    def make(keyword, blogs):
        calls.append(keyword)
        if keyword in blocked:
            raise bd.UnverifiedKeyword(f"'{keyword}' 미확인")
        return len(blogs)
    return make, calls


def test_skips_unverified_and_uses_next(env):
    make, calls = _make({"증여세 계산"})
    assert bd._with_retries("info", "", make, ["A", "C"]) == 2
    assert calls == ["증여세 계산", "재산세 계산"]
    used = bd._used()
    assert used["증여세 계산"]["skipped"] == "unverified"
    assert "skipped" not in used["재산세 계산"]


def test_all_blocked_logs_error(env):
    make, calls = _make({"증여세 계산", "재산세 계산", "실업급여 신청"})
    assert bd._with_retries("info", "", make, ["A"]) == 0
    assert len(calls) == bd.KEYWORD_TRIES
    assert any(level == "error" for level, _ in env)


def test_manual_keyword_is_not_replaced(env):
    make, calls = _make({"증여세 계산"})
    assert bd._with_retries("info", "증여세 계산", make, ["A"]) == 0
    assert calls == ["증여세 계산"]


def test_budget_stops_retries(env, monkeypatch):
    monkeypatch.setattr(bd, "_STARTED", bd.time.monotonic() - bd.RETRY_BUDGET_SEC - 1)
    make, calls = _make({"증여세 계산", "재산세 계산"})
    assert bd._with_retries("info", "", make, ["A"]) == 0
    assert calls == ["증여세 계산"]


def test_reject_unverified_only_for_marker():
    base = {"title": "t", "sections": [{}] * 4, "sources": [{"title": "s", "url": "u"}],
            "intro": "가" * 1600}
    bd._reject_unverified("k", "A", base)   # 통과
    with pytest.raises(bd.UnverifiedKeyword):
        bd._reject_unverified("k", "A", dict(base, intro="금액은 공식 확인 필요 " + "가" * 1600))


def test_pick_filters_cooldown_before_truncating(tmp_path, monkeypatch):
    """상위 후보가 전부 쿨다운 씨앗이어도 그 아래 후보를 고른다 (11-11~17 가격형 0건 회귀)."""
    import json
    from datetime import date
    used = tmp_path / "used.json"
    used.write_text(json.dumps({"가습기추천": {"kind": "price", "day": date.today().isoformat()}},
                               ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bd, "USED", used)
    rows = [{"keyword": f"가습기{i}", "score": 100 - i} for i in range(50)] + [{"keyword": "제습기추천", "score": 1}]

    def fake_plan(kind, used=None, top=30):
        return rows if top is None else rows[:top]
    monkeypatch.setattr(bd, "plan", fake_plan)
    monkeypatch.setattr(bd, "seed_of", lambda k, kind: "가습기" if "가습기" in k else "제습기")
    assert bd._pick("price")["keyword"] == "제습기추천"
