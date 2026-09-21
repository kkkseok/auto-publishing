"""Offline regression checks for the actual gaps in the September appeal."""
import json
import sqlite3
from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest
import requests

from common import threads_access as access, threads_guard as guard, threads_approval as approval
from publishers.threads import ThreadsPublisher


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import os
    for key in list(os.environ):
        if key.startswith("THREADS_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(access, "_PATH", tmp_path / "access.json")
    monkeypatch.setattr(access, "_session_hold", "")
    monkeypatch.setattr(guard, "_STATE_PATH", tmp_path / "guard.json")
    monkeypatch.setattr(requests.sessions.Session, "request",
                        Mock(side_effect=AssertionError("Network forbidden in regression tests")))


def activate():
    access._write({"status": "active", "restoration_reference": "offline-test-only"})


def test_hold_blocks_all_publisher_and_token_requests(monkeypatch):
    with patch("dotenv.load_dotenv"):
        from common import threads_token as token
    send = Mock(side_effect=AssertionError("Approval must not be requested during hold"))
    monkeypatch.setattr(approval, "request", send)
    pub = ThreadsPublisher("test-user", "test-token")
    assert not pub.login()
    assert not pub.post("", "본문" * 100).success
    assert not pub.post_reply("parent", "reply").success
    assert pub._create_container("text", "TEXT") is None
    assert not pub._publish_container("container").success
    assert not pub._fetch_permalink("post")
    assert pub.get_profile() == {}
    assert token.check_token_status()["valid"] is False
    assert token.refresh_long_lived_token() is None
    assert token.exchange_short_to_long("test") is None
    assert token.get_short_lived_token("test") is None
    assert token.run_oauth_flow() is None
    send.assert_not_called()


def test_corrupt_access_state_blocks():
    access._PATH.write_text("broken", encoding="utf-8")
    assert access.blocked_reason()


def test_api_deactivation_persists_hold(monkeypatch):
    activate()
    access.observe_error('{"error":{"message":"API access deactivated"}}')
    monkeypatch.setattr(access, "_session_hold", "")  # simulate a fresh process
    assert access.blocked_reason()
    assert json.loads(access._PATH.read_text(encoding="utf-8"))["status"] == "suspended"


def test_disabled_gates_block_instead_of_bypass(monkeypatch):
    monkeypatch.setenv("THREADS_GUARD_ENABLED", "false")
    monkeypatch.setenv("THREADS_APPROVAL_REQUIRED", "false")
    assert guard.precheck("본문" * 100)[0] is False
    assert approval.gate("본문" * 100)[0] is False


def test_approval_from_other_chat_cannot_authorize(monkeypatch, tmp_path):
    monkeypatch.setattr(approval, "_PATH", tmp_path / "approvals.json")
    approval._save({"pending": {"tg_message_id": 42, "tg_chat_id": "123",
                               "status": "pending"}})
    assert not approval.resolve_by_tg_message_id(42, "ok", chat_id=999)
    assert not approval.resolve_by_tg_message_id(42, "ok")
    assert approval._load()["pending"]["status"] == "pending"
    assert approval.resolve_by_tg_message_id(42, "ok", chat_id=123)
    assert approval._load()["pending"]["status"] == "approved"


def test_affiliate_ratio_applies_from_first_post():
    assert guard.check_quota(True)[0] is False
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    guard._save({yesterday: {"total": 2, "affiliate": 0}})
    assert guard.check_quota(True)[0] is False  # 1/3 > 30%
    guard._save({yesterday: {"total": 3, "affiliate": 0}})
    assert guard.check_quota(True)[0] is True  # 1/4 <= 30%


def test_settings_cannot_relax_limits(monkeypatch):
    monkeypatch.setenv("THREADS_MAX_PER_DAY", "999")
    monkeypatch.setenv("THREADS_MAX_AFFILIATE_PER_DAY", "999")
    monkeypatch.setenv("THREADS_MAX_AFFILIATE_RATIO", "1")
    monkeypatch.setenv("THREADS_MIN_CHARS", "1")
    guard._save({date.today().isoformat(): {"total": 3, "affiliate": 0}})
    assert guard.check_quota(False)[0] is False
    assert guard.check_quality("짧은 문장")[0] is False
    assert guard.today_usage()["max_ratio"] == 0.30
    assert guard.today_usage()["max_affiliate"] == 1
    monkeypatch.setenv("THREADS_MAX_AFFILIATE_RATIO", "nan")
    assert guard.today_usage()["max_ratio"] == 0.30


@pytest.mark.parametrize("content", ['broken', '[]', '{"2026-09-21":{"total":-1,"affiliate":0}}'])
def test_corrupt_quota_state_blocks(content):
    guard._STATE_PATH.write_text(content, encoding="utf-8")
    assert guard.check_quota(False)[0] is False


def test_rolling_window_excludes_day_31_and_future():
    today = date.today()
    data = {(today - timedelta(days=29)).isoformat(): {"total": 3, "affiliate": 0},
            (today - timedelta(days=30)).isoformat(): {"total": 20, "affiliate": 0},
            (today + timedelta(days=1)).isoformat(): {"total": 20, "affiliate": 0}}
    assert guard._window_totals(data) == (3, 0)


def test_concurrent_publisher_cannot_pass_quota_lock():
    with guard.publication_lock():
        with pytest.raises(sqlite3.OperationalError):
            with guard.publication_lock():
                pytest.fail("Concurrent quota access")
    with guard.publication_lock():
        pass  # lock released after the first publisher exits


def test_suspension_during_approval_blocks_api(monkeypatch):
    activate()
    def approve(*args):
        access.suspend("operator suspended during review")
        return True, ""
    monkeypatch.setattr(approval, "gate", approve)
    result = ThreadsPublisher("test", "test").post("", "한국어 본문 검토입니다. " * 15)
    assert result.success is False
    assert "Threads API" in result.message


def test_active_reply_path_still_disabled():
    activate()
    pub = ThreadsPublisher("test", "test")
    assert not pub.post_reply("parent", "text").success
    assert pub._create_container("text", "TEXT", reply_to_id="parent") is None


def test_successful_approved_post_records_quota(monkeypatch):
    activate()
    monkeypatch.setattr(approval, "gate", lambda *args: (True, ""))
    monkeypatch.setattr("publishers.threads.time.sleep", lambda _: None)
    post = Mock(side_effect=[Mock(ok=True, json=lambda: {"id": "container"}),
                             Mock(ok=True, json=lambda: {"id": "post"})])
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(
        ok=True, json=lambda: {"permalink": "https://www.threads.net/@test/post/test"})))
    result = ThreadsPublisher("test", "test").post("", "한국어 본문 검토입니다. " * 15)
    assert result.success is True
    assert guard.today_usage()["total"] == 1
    assert post.call_count == 2


def test_quota_write_failure_holds_future_posts(monkeypatch):
    activate()
    monkeypatch.setattr(guard, "record_published", Mock(side_effect=OSError("disk full")))
    ThreadsPublisher._record(False)
    assert access.blocked_reason()
