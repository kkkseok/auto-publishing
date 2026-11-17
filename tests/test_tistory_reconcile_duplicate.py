"""실패 보고된 티스토리 발행을 재시도하기 전에 실제 발행 여부를 확인 — tistory_publish.reconcile.

11-17 진단: 확장이 '발행 완료 navigation timeout' 으로 실패를 보고했지만 실제로는 발행돼,
다음 일정의 재시도가 같은 글을 한 번 더 올렸다 (중복 5쌍).
- RSS 에 같은 제목이 있으면 재시도하지 않고 published 로 기록
- 없으면 종전처럼 ready 로 돌려 재시도
- RSS 를 못 읽으면 건드리지 않고 error 로그 (중복보다 지연이 낫다)
"""
import pytest

from pipelines import tistory_publish as tp

RSS = """<rss><channel>
<item><title>미니 폼롤러 추천 가이드, EVA&amp;middot;EPP 소재와 크기 고르는 기준 (2026년 11월 기준)</title>
<link>https://pricerader.tistory.com/entry/foam</link></item>
<item><title>덤벨 구매 가이드</title><link>https://pricerader.tistory.com/entry/dumbbell</link></item>
</channel></rss>"""
TITLE = "미니 폼롤러 추천 가이드, EVA·EPP 소재와 크기 고르는 기준 (2026년 11월 기준)"


class _Resp:
    def __init__(self, text, ok=True):
        self.text, self.ok = text, ok

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError("503")


@pytest.fixture
def env(monkeypatch):
    import requests
    drafts = {"d1": {"status": "queued_tistory", "queue_id": "q1", "title": TITLE, "blog": "D"}}
    queue = {"q1": {"id": "q1", "status": "failed", "blog_name": "pricerader", "title": TITLE,
                    "error": "발행 완료 navigation timeout — 5분 내 캡차/페이지 이동 없음"}}
    calls = {"rss": RSS, "ok": True, "added": [], "logs": []}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(calls["rss"], calls["ok"]))
    monkeypatch.setattr(tp.blog_drafts, "queued", lambda roles: [(k, dict(v)) for k, v in drafts.items()])
    monkeypatch.setattr(tp.blog_drafts, "update", lambda did, **f: drafts[did].update(f))
    monkeypatch.setattr(tp.blog_drafts, "notify", lambda text: None)
    monkeypatch.setattr(tp.tistory_queue, "get", lambda qid: dict(queue[qid]))
    monkeypatch.setattr(tp.tistory_queue, "mark_done", lambda qid, url, post_id="": queue[qid].update(
        status="done", result_url=url))
    import common.publish_queue as pq
    monkeypatch.setattr(pq, "add_url", lambda url, **k: calls["added"].append(url))
    monkeypatch.setattr(tp, "log", lambda msg, level="info": calls["logs"].append((level, msg)))
    return drafts, queue, calls


def test_published_despite_failure_is_not_retried(env):
    drafts, queue, calls = env
    tp.reconcile()
    assert drafts["d1"]["status"] == "published"
    assert drafts["d1"]["url"] == "https://pricerader.tistory.com/entry/foam"
    assert queue["q1"]["status"] == "done"
    assert calls["added"] == ["https://pricerader.tistory.com/entry/foam"]


def test_not_published_goes_back_to_ready(env):
    drafts, _, calls = env
    calls["rss"] = RSS.replace("미니 폼롤러", "다른 글")
    tp.reconcile()
    assert drafts["d1"]["status"] == "ready" and drafts["d1"]["tries"] == 1


def test_feed_error_leaves_item_untouched(env):
    drafts, _, calls = env
    calls["ok"] = False
    tp.reconcile()
    assert drafts["d1"]["status"] == "queued_tistory"
    assert any(level == "error" for level, _ in calls["logs"])
