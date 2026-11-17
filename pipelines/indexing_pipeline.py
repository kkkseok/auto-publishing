"""
파이프라인: 색인 등록 (Google + Naver)

publish_queue.json 의 미색인 URL 을 Google Indexing API + Naver Search Advisor 에 제출.
백링크는 별도 파이프라인(backlink_pipeline.py)에서 처리.

실행:
    python -m pipelines.indexing_pipeline

환경변수 (.env):
    SCHEDULE_INDEX                 = 22:00
    GOOGLE_INDEXING_SA_JSON        = /path/to/sa.json   (또는 도메인별 SA 키)
    NAVER_INDEX_ASIDE_ACCOUNT      = u0   (서치어드바이저에 로그인된 Aside 계정, 비우면 기본)

네이버 제출은 Aside 브라우저로 한다 (common/indexing_naver.py) — 자동 로그인은 10-14 부터 막혔다.
제출 전에 비공개·삭제된 글(로그인 없이 200 이 아닌 글)은 대기열에서 영구 제외한다.
"""
import os

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.publish_queue import (
    get_pending, mark_done_bulk, mark_status_bulk, mark_skipped_bulk, stats,
)
from common.notifier import notify_pipeline_result


SCHEDULE = {
    "env":  "SCHEDULE_INDEX",
    "func": "run",
}

# 소유하지 않은(=Google/Naver 색인 불가) 도메인. 본인 소유로 확인된 사이트만
# 색인 제출이 의미 있으므로, threads 등 SNS URL 은 색인 대기열에서 제외한다.
# (INDEX_EXCLUDE_DOMAINS 로 .env 에서 덮어쓰기 가능 — 콤마 구분)
_DEFAULT_INDEX_EXCLUDE = (
    "threads.com,threads.net,twitter.com,x.com,"
    "instagram.com,pinterest.com,facebook.com,"
    # 네이버 블로그는 내 소유 사이트가 아니라 제출할 수 없다 — 네이버가 스스로 수집한다
    "blog.naver.com"
)


def _excluded_index_domains() -> set:
    raw = os.getenv("INDEX_EXCLUDE_DOMAINS", _DEFAULT_INDEX_EXCLUDE)
    out = set()
    for d in raw.split(","):
        d = d.strip().lower()
        if d.startswith("www."):
            d = d[4:]
        if d:
            out.add(d)
    return out


def _indexable(url: str) -> bool:
    """색인 대상(소유 도메인)인지. 제외 도메인이면 False."""
    from urllib.parse import urlparse
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return not any(host == d or host.endswith("." + d)
                   for d in _excluded_index_domains())


def _google_api_enabled() -> bool:
    """Google Indexing API 는 채용공고·라이브 방송 페이지용이다 — 블로그 글은 응답이 ok 여도
    색인되지 않았다(11-17: ok 받은 kkkseok 6건을 구글이 '알려지지 않은 URL' 로 봄).
    티스토리 사이트맵이 서치콘솔에 등록돼 매일 수집되므로 기본은 끄고 사이트맵에 맡긴다."""
    return os.getenv("GOOGLE_INDEXING_API", "false").strip().lower() in ("1", "true", "yes")


def _by_original(results: dict, pending: list) -> dict:
    """제출한 주소(canonical) 기준 결과를 대기열의 원래 주소 기준으로 돌린다."""
    back = {it.get("_submit", it["url"]): it["url"] for it in pending}
    return {back.get(u, u): r for u, r in results.items()}


def _submit_google(pending: list) -> list:
    """Google 색인 제출. 성공한 URL 목록 반환.

    실패 케이스(no_permission/limit/error)는 publish_queue 에 상세 상태로
    저장되어 dashboard 에서 사유 노출.
    """
    if not pending:
        log("[색인] Google 미제출 URL 없음", "info")
        return []
    if not _google_api_enabled():
        n = mark_skipped_bulk([it["url"] for it in pending], "google_indexed", status="sitemap")
        log(f"[색인] Google: Indexing API 끔 — {n}건은 서치콘솔 사이트맵으로 수집 (GOOGLE_INDEXING_API)", "info")
        return []
    try:
        from common.indexing_google import submit_urls
        pending = pending[:200]
        urls = [item.get("_submit", item["url"]) for item in pending]
        results = _by_original(submit_urls(urls), pending)
        # 모든 결과를 publish_queue 에 기록 (성공/실패 모두) — 사유 보존
        mark_status_bulk(results, "google_indexed")
        ok = [u for u, s in results.items() if s == "ok"]
        log(f"[색인] Google: {len(ok)}/{len(urls)}건 성공", "step")
        return ok
    except Exception as e:
        log(f"[색인] Google 오류: {e}", "error")
        return []


def _submit_naver(pending: list) -> list:
    """Naver 색인 제출. 성공한 URL 목록 반환."""
    if not pending:
        log("[색인] Naver 미제출 URL 없음", "info")
        return []
    try:
        from common.indexing_naver import submit_urls
        pending = pending[:50]
        urls = [item.get("_submit", item["url"]) for item in pending]
        results = _by_original(submit_urls(urls), pending)
        mark_status_bulk(results, "naver_indexed")
        ok = [u for u, s in results.items() if s == "ok"]
        log(f"[색인] Naver: {len(ok)}/{len(urls)}건 성공", "step")
        return ok
    except Exception as e:
        log(f"[색인] Naver 오류: {e}", "error")
        return []


def _public_canonical(url: str) -> str | None:
    """로그인 없이 열리면 제출할 주소(canonical), 비공개·삭제(200 아님)면 None.

    티스토리는 발행 기록이 /41 같은 숫자 주소지만 canonical 은 /entry/<제목> 이다. 숫자 주소를
    제출하면 검색엔진이 canonical 로 다시 찾아가야 해서 제출 효과가 약하다 — canonical 을 낸다.
    """
    import html
    import re
    import requests
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException:
        return url   # 일시 오류로 영구 제외하지 않는다 — 원래 주소로 제출
    if r.status_code != 200:
        return None
    m = re.search(r'<link rel="canonical" href="([^"]+)"', r.text)
    return html.unescape(m.group(1)) if m else url


def _filter_indexable(pending: list, field: str) -> list:
    """미소유 도메인(threads 등)·비공개 글을 SKIP 처리해 대기열에서 영구 제외하고,
    실제 색인 대상(소유 사이트의 공개 글) 목록만 반환한다."""
    excluded = [it.get("url", "") for it in pending if not _indexable(it.get("url", ""))]
    if excluded:
        n = mark_skipped_bulk(excluded, field)
        log(f"[색인] 미소유 도메인 {n}건 제외(SKIP) — {field}", "info")
    owned = [it for it in pending if _indexable(it.get("url", ""))]
    out, hidden = [], []
    for it in owned:
        canon = _public_canonical(it.get("url", ""))
        if canon is None:
            hidden.append(it.get("url", ""))
        else:
            out.append(dict(it, _submit=canon))
    if hidden:
        n = mark_skipped_bulk(hidden, field)
        log(f"[색인] 비공개·삭제 글 {n}건 제외(SKIP) — {field}", "info")
    return out


def run() -> None:
    """색인 등록 파이프라인 1회 실행."""
    log("=== 색인 등록 파이프라인 시작 ===", "step")

    log("=== 1단계: Google 색인 제출 ===", "step")
    google_pending = _filter_indexable(get_pending("google_indexed"), "google_indexed")
    google_ok = _submit_google(google_pending)

    log("=== 2단계: Naver 색인 제출 ===", "step")
    naver_pending = _filter_indexable(get_pending("naver_indexed"), "naver_indexed")
    naver_ok = _submit_naver(naver_pending)

    log(f"큐 통계: {stats()}", "info")

    total_attempted = len(google_pending) + len(naver_pending)
    total_succeeded = len(google_ok) + len(naver_ok)
    reason = "empty" if total_attempted == 0 else "failure"
    notify_pipeline_result(
        "색인 등록",
        total_succeeded,
        total_attempted,
        details=(
            f"Google {len(google_ok)}/{len(google_pending)} · "
            f"Naver {len(naver_ok)}/{len(naver_pending)}"
        ),
        reason=reason,
    )
    log("=== 색인 등록 파이프라인 완료 ===", "step")


if __name__ == "__main__":
    run()
