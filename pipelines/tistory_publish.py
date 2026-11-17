"""발행 대기(ready) 티스토리 초안을 하나 크롬 확장 대기열로 보낸다.

blog_drafts_daily 가 만든 C·D 초안을 사용자가 깨어 있는 시간(SCHEDULE_TISTORY_PUBLISH)에
내보내, 확장이 발행할 때 뜨는 캡차 알림(텔레그램)을 놓치지 않게 한다. 한 번에 하나만.

수동 실행: python -m pipelines.tistory_publish
"""
import html
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts, tistory_queue
from common.logger import log
from pipelines.blog_job import TISTORY_ROLE, check_draft, publish

SCHEDULE = {
    "env":  "SCHEDULE_TISTORY_PUBLISH",
    "func": "run",
}


MAX_TRIES = 3


class FeedError(RuntimeError):
    pass


def _norm_title(s: str) -> str:
    # RSS 제목은 &amp;middot; 처럼 두 번 이스케이프돼 온다 — 한글·영숫자만 남겨 비교
    return re.sub(r"[^0-9A-Za-z가-힣]", "", html.unescape(html.unescape(s or "")))


def find_published(blog_name: str, title: str) -> str:
    """블로그 RSS(최근 글 10개)에 같은 제목의 공개 글이 있으면 그 주소, 없으면 "".

    확장이 '발행 완료 navigation timeout' 으로 실패를 보고해도 실제로는 발행된 경우가 있었다.
    그걸 그대로 재시도하면 같은 글이 두 번 올라간다 (10-16~11-07 중복 5쌍, 11-17 진단).
    """
    import requests
    try:
        r = requests.get(f"https://{blog_name}.tistory.com/rss", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        raise FeedError(f"{blog_name} RSS 조회 실패: {e}") from e
    want = _norm_title(title)
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        link = re.search(r"<link>(.*?)</link>", it, re.S)
        if t and link and want and _norm_title(t.group(1)) == want:
            return link.group(1).strip()
    return ""


def _mark_found(did: str, item: dict, q: dict, url: str) -> None:
    """실패로 보고됐지만 실제로 발행된 글 — 브릿지 /done 이 했을 기록을 대신 남긴다."""
    from common.publish_queue import add_url
    tistory_queue.mark_done(q["id"], url=url)
    blog_drafts.update(did, status="published", url=url, error="")
    add_url(url, platform="tistory", title=q.get("title", ""), keyword=q.get("keyword", ""),
            source=q.get("source", ""), affiliate_url=q.get("affiliate_url", ""))
    log(f"[티스토리] 실패 보고였지만 실제 발행됨 — 재시도 안 함 {did}: {url}", "ok")
    blog_drafts.notify(f"✅ 티스토리 발행 확인 (확장은 실패로 보고)\n{item['title']}\n{url}")


def reconcile() -> None:
    """확장이 처리한 결과를 초안 기록에 반영 — 성공은 주소를, 실패는 재시도 대기(ready)로.

    실패를 재시도 대기로 돌리기 전에 블로그에 이미 올라갔는지 확인한다. 확인할 수 없으면
    (RSS 오류) 이번에는 건드리지 않는다 — 다시 보내 중복을 만드는 것보다 한 번 늦는 게 낫다.
    """
    for did, item in blog_drafts.queued(tuple(TISTORY_ROLE)):
        q = tistory_queue.get(item.get("queue_id", "")) if item.get("queue_id") else None
        if not q:
            continue
        if q["status"] == "done":
            blog_drafts.update(did, status="published", url=q.get("result_url", ""))
        elif q["status"] == "failed":
            try:
                url = find_published(q["blog_name"], q.get("title") or item["title"])
            except FeedError as e:
                log(f"[티스토리] 실패 건 발행 여부 확인 불가 — 다음 일정에 다시 확인 {did}: {e}", "error")
                continue
            if url:
                _mark_found(did, item, q, url)
                continue
            tries = item.get("tries", 0) + 1
            if tries < MAX_TRIES:
                blog_drafts.update(did, status="ready", tries=tries, error=q.get("error", "")[:300])
                log(f"[티스토리] 실패 → 다음 일정에 재시도 ({tries}/{MAX_TRIES}) {did}: {q.get('error', '')[:120]}", "warn")
            else:
                blog_drafts.update(did, status="failed", tries=tries, error=q.get("error", "")[:300])
                blog_drafts.notify(f"❌ 티스토리 발행 {MAX_TRIES}회 실패: {item['title']}\n{q.get('error', '')[:200]}")
                log(f"[티스토리] {MAX_TRIES}회 실패 — 포기 {did}", "error")


def _quality_problems(draft_id: str) -> list[str]:
    item = blog_drafts.get(draft_id) or {}
    path = Path(item.get("dir") or "") / f"{item.get('blog')}_draft.json"
    try:
        return check_draft(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        return [f"초안 파일 읽기 실패: {e}"]


def _first_publishable(ids: list[str]) -> tuple[str, int]:
    """품질 기준을 통과하는 가장 오래된 대기 초안 — 못 넘는 것은 failed 로 내려 다음 일정을 막지 않게.

    10-17~19 '확인 필요'가 남은 초안이 대기열 맨 앞에 서서 발행 시간대를 하나씩 날렸다.
    """
    dropped = 0
    for did in ids:
        problems = _quality_problems(did)
        if not problems:
            return did, dropped
        blog_drafts.update(did, status="failed", error=("품질 기준 미달 — " + ", ".join(problems))[:500])
        log(f"[티스토리] 대기 초안 제외 {did}: {', '.join(problems)}", "warn")
        dropped += 1
    return "", dropped


def run() -> None:
    reconcile()
    ids = blog_drafts.ready(tuple(TISTORY_ROLE))
    if not ids:
        log("[티스토리] 발행 대기 초안 없음", "info")
        return
    did, dropped = _first_publishable(ids)
    if not did:
        log(f"[티스토리] 대기 초안 {dropped}건 모두 품질 기준 미달 — 이번 일정 발행 없음", "error")
        return
    log(f"[티스토리] 대기 {len(ids) - dropped}건 중 1건 확장 대기열로 — {did}"
        + (f" (품질 미달 {dropped}건 제외)" if dropped else ""), "step")
    if publish(did):
        log("[티스토리] 확장 대기열 등록 완료 (발행 완료 알림은 브릿지가 보냄)", "ok")
    else:
        log("[티스토리] 발행 실패 — data/blog_drafts.json 참고", "error")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run()
