"""발행 대기(ready) 티스토리 초안을 하나 크롬 확장 대기열로 보낸다.

blog_drafts_daily 가 만든 C·D 초안을 사용자가 깨어 있는 시간(SCHEDULE_TISTORY_PUBLISH)에
내보내, 확장이 발행할 때 뜨는 캡차 알림(텔레그램)을 놓치지 않게 한다. 한 번에 하나만.

수동 실행: python -m pipelines.tistory_publish
"""
import sys

from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts
from common.logger import log
from pipelines.blog_job import TISTORY_ROLE, publish

SCHEDULE = {
    "env":  "SCHEDULE_TISTORY_PUBLISH",
    "func": "run",
}


def run() -> None:
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
