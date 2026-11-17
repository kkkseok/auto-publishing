"""색인 제출 — 정식 주소(canonical)로 내고, 구글 Indexing API 는 기본으로 끈다 (11-17 진단).

- 공개 글은 canonical 을 _submit 으로 달고, 비공개(403)는 SKIP
- 제출 결과는 대기열의 원래 주소(/41) 기준으로 기록된다
- GOOGLE_INDEXING_API 가 꺼져 있으면 API 를 부르지 않고 status='sitemap' 으로 SKIP
"""
import pytest

from pipelines import indexing_pipeline as ip

NUM = "https://pricerader.tistory.com/41"
CANON = "https://pricerader.tistory.com/entry/ice-box"
HIDDEN = "https://pricerader.tistory.com/25"


class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


@pytest.fixture
def env(monkeypatch):
    import requests
    pages = {NUM: _Resp(200, f'<link rel="canonical" href="{CANON}"/>'), HIDDEN: _Resp(403)}
    monkeypatch.setattr(requests, "get", lambda url, **k: pages[url])
    skipped, logs = [], []
    monkeypatch.setattr(ip, "mark_skipped_bulk",
                        lambda urls, field, status="excluded_domain": skipped.append((list(urls), field, status)) or len(urls))
    monkeypatch.setattr(ip, "log", lambda msg, level="info": logs.append((level, msg)))
    return skipped


def test_filter_attaches_canonical_and_skips_hidden(env):
    out = ip._filter_indexable([{"url": NUM}, {"url": HIDDEN}], "naver_indexed")
    assert out == [{"url": NUM, "_submit": CANON}]
    assert env == [([HIDDEN], "naver_indexed", "excluded_domain")]


def test_results_map_back_to_queue_url():
    pending = [{"url": NUM, "_submit": CANON}]
    assert ip._by_original({CANON: "ok"}, pending) == {NUM: "ok"}


def test_naver_submits_canonical(env, monkeypatch):
    import common.indexing_naver as naver
    sent, recorded = [], {}
    monkeypatch.setattr(naver, "submit_urls", lambda urls: sent.extend(urls) or {u: "ok" for u in urls})
    monkeypatch.setattr(ip, "mark_status_bulk", lambda results, field: recorded.update(results))
    assert ip._submit_naver([{"url": NUM, "_submit": CANON}]) == [NUM]
    assert sent == [CANON] and recorded == {NUM: "ok"}


def test_google_api_off_by_default(env, monkeypatch):
    monkeypatch.delenv("GOOGLE_INDEXING_API", raising=False)
    import common.indexing_google as g
    monkeypatch.setattr(g, "submit_urls", lambda urls: pytest.fail("Indexing API 를 부르면 안 됨"))
    assert ip._submit_google([{"url": NUM, "_submit": CANON}]) == []
    assert env == [([NUM], "google_indexed", "sitemap")]
