"""대기열 저장·등록 실패 처리 (11-17 12:30: WinError 5 로 발행이 죽고 초안이 publishing 에 멈춤).

- os.replace 가 잠깐 PermissionError 를 내면 다시 시도해 저장한다
- 계속 실패하면 예외를 올린다 (조용히 삼키지 않음)
- 대기열 등록 중 예외면 초안을 ready 로 되돌리고 예외를 올린다
"""
import json

import pytest

from common import tistory_queue as tq


def test_save_retries_transient_permission_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "QUEUE_PATH", tmp_path / "q.json")
    monkeypatch.setattr(tq.time, "sleep", lambda s: None)
    real, calls = tq.os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "액세스가 거부되었습니다")
        real(src, dst)
    monkeypatch.setattr(tq.os, "replace", flaky)
    tq._save([{"id": "a"}])
    assert json.loads((tmp_path / "q.json").read_text(encoding="utf-8")) == [{"id": "a"}]
    assert len(calls) == 3


def test_save_raises_when_lock_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(tq, "QUEUE_PATH", tmp_path / "q.json")
    monkeypatch.setattr(tq.time, "sleep", lambda s: None)
    monkeypatch.setattr(tq.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(5, "denied")))
    with pytest.raises(PermissionError):
        tq._save([{"id": "a"}])


def test_enqueue_exception_puts_draft_back_to_ready(monkeypatch):
    from pipelines import blog_job as bj
    store = {"d1": {"blog": "C", "keyword": "실업급여", "dir": "x", "status": "ready"}}
    draft = {"title": "t", "tags": [], "sections": []}
    monkeypatch.setattr(bj.blog_drafts, "get", lambda did: dict(store[did]))
    monkeypatch.setattr(bj.blog_drafts, "update", lambda did, **f: store[did].update(f))
    monkeypatch.setattr(bj, "check_draft", lambda d: [])
    monkeypatch.setattr(bj, "render_html", lambda *a, **k: "<p>b</p>")
    monkeypatch.setattr(bj, "_thumbnail", lambda *a, **k: "")
    monkeypatch.setattr(bj.json, "loads", lambda s: draft)
    monkeypatch.setattr(bj.Path, "read_text", lambda self, encoding=None: "{}")
    monkeypatch.setattr(bj.Path, "exists", lambda self: False)

    class Boom:
        def __init__(self, name): pass
        def post(self, *a, **k): raise PermissionError(5, "denied")
    import publishers.tistory_bridge as tb
    monkeypatch.setattr(tb, "TistoryBridgePublisher", Boom)
    with pytest.raises(PermissionError):
        bj.publish("d1")
    assert store["d1"]["status"] == "ready" and "denied" in store["d1"]["error"]
