"""발행 대기(ready) 티스토리 초안을 하나 크롬 확장 대기열로 보낸다.

blog_drafts_daily 가 만든 C·D 초안을 사용자가 깨어 있는 시간(SCHEDULE_TISTORY_PUBLISH)에
내보내, 확장이 발행할 때 뜨는 캡차 알림(텔레그램)을 놓치지 않게 한다. 한 번에 하나만.

수동 실행: python -m pipelines.tistory_publish
"""
import json
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
