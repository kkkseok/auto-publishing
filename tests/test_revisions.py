"""기존 글 수정안 승인 테스트.

목표: 승인 답장만 반영 대기로 넘기고, 다른 채팅·다른 메시지 답장은 건드리지 않는다.
수정 지시에 실제 수치·검색어가 들어간다.
"""
import pytest

from common import revisions
from pipelines.content_revise import instructions


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(revisions, "PATH", tmp_path / "revisions.json")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    rid = revisions.add(url="https://c.tistory.com/1", draft_id="d1", blog="C", reason="low_ctr")
    revisions.update(rid, tg_message_id=777, tg_chat_id="42")
    return rid


def test_approve_reply(store):
    assert revisions.resolve_by_tg_message_id(777, "승인", chat_id=42) is True
    assert revisions.all_()[store]["status"] == "approved"


def test_other_reply_rejects(store):
    assert revisions.resolve_by_tg_message_id(777, "제목이 좀 별로", chat_id=42) is True
    assert revisions.all_()[store]["status"] == "rejected"


def test_unrelated_message_or_chat_is_ignored(store):
    assert revisions.resolve_by_tg_message_id(778, "승인", chat_id=42) is False
    assert revisions.resolve_by_tg_message_id(777, "승인", chat_id=99) is False
    assert revisions.all_()[store]["status"] == "proposed"
    assert revisions.pending_urls() == {"https://c.tistory.com/1"}


def test_decided_revision_is_not_resolved_twice(store):
    revisions.resolve_by_tg_message_id(777, "승인", chat_id=42)
    assert revisions.resolve_by_tg_message_id(777, "취소", chat_id=42) is False
    assert revisions.all_()[store]["status"] == "approved"


def test_instructions_carry_real_numbers():
    post = {"impressions": 240, "clicks": 2, "ctr": 0.0083, "position": 6.2,
            "top_queries": [{"query": "근로장려금 재산 기준"}, {"query": "근로장려금 기한 후 신청"}]}
    low = instructions("low_ctr", post)
    assert "노출 240회" in low and "CTR 0.8%" in low and "근로장려금 재산 기준" in low
    assert "title" in low
    assert "보강" in instructions("striking", post)
