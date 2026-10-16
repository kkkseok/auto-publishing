"""발행 대기(ready) 티스토리 초안을 하나 크롬 확장 대기열로 보낸다.

blog_drafts_daily 가 만든 C·D 초안을 사용자가 깨어 있는 시간(SCHEDULE_TISTORY_PUBLISH)에
내보내, 확장이 발행할 때 뜨는 캡차 알림(텔레그램)을 놓치지 않게 한다. 한 번에 하나만.

수동 실행: python -m pipelines.tistory_publish
"""
import sys

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts, tistory_queue
from common.logger import log
from pipelines.blog_job import TISTORY_ROLE, publish

SCHEDULE = {
    "env":  "SCHEDULE_TISTORY_PUBLISH",
    "func": "run",
}


MAX_TRIES = 3


def reconcile() -> None:
    """확장이 처리한 결과를 초안 기록에 반영 — 성공은 주소를, 실패는 재시도 대기(ready)로."""
    for did, item in blog_drafts.queued(tuple(TISTORY_ROLE)):
        q = tistory_queue.get(item.get("queue_id", "")) if item.get("queue_id") else None
        if not q:
            continue
        if q["status"] == "done":
            blog_drafts.update(did, status="published", url=q.get("result_url", ""))
        elif q["status"] == "failed":
            tries = item.get("tries", 0) + 1
            if tries < MAX_TRIES:
                blog_drafts.update(did, status="ready", tries=tries, error=q.get("error", "")[:300])
                log(f"[티스토리] 실패 → 다음 일정에 재시도 ({tries}/{MAX_TRIES}) {did}: {q.get('error', '')[:120]}", "warn")
            else:
                blog_drafts.update(did, status="failed", tries=tries, error=q.get("error", "")[:300])
                blog_drafts.notify(f"❌ 티스토리 발행 {MAX_TRIES}회 실패: {item['title']}\n{q.get('error', '')[:200]}")
                log(f"[티스토리] {MAX_TRIES}회 실패 — 포기 {did}", "error")


def run() -> None:
    reconcile()
    ids = blog_drafts.ready(tuple(TISTORY_ROLE))
    if not ids:
        log("[티스토리] 발행 대기 초안 없음", "info")
        return
    log(f"[티스토리] 대기 {len(ids)}건 중 1건 확장 대기열로 — {ids[0]}", "step")
    if publish(ids[0]):
        log("[티스토리] 확장 대기열 등록 완료 (발행 완료 알림은 브릿지가 보냄)", "ok")
    else:
        log("[티스토리] 발행 실패 — data/blog_drafts.json 참고", "error")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run()
