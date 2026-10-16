"""티스토리용 추가 글 — 하루 1회 더 생활정보(C)·가격형(D) 글을 만들어 발행 대기에 넣는다.

애드센스 심사 전에 티스토리 글 수를 빨리 늘리려고 (10-16 사용자 결정: 하루 2개) 네이버는
그대로 두고 티스토리만 한 번 더 돈다. 키워드는 아침 실행과 겹치지 않는다(사용 기록 공유).
발행은 pipelines.tistory_publish 가 SCHEDULE_TISTORY_PUBLISH 의 각 시각에 1건씩 한다.

수동 실행: python -m pipelines.blog_drafts_extra
스케줄: SCHEDULE_BLOG_EXTRA  ·  블로그: BLOG_EXTRA_BLOGS(기본 C), BLOG_EXTRA_PRICE_BLOGS(기본 D)
"""
import sys

from dotenv import load_dotenv
load_dotenv()

from pipelines.blog_drafts_daily import _blogs
from pipelines.blog_drafts_daily import run as run_daily

SCHEDULE = {
    "env":  "SCHEDULE_BLOG_EXTRA",
    "func": "run",
}


def run() -> None:
    run_daily(info_blogs=_blogs("BLOG_EXTRA_BLOGS", "C"), price_blogs=_blogs("BLOG_EXTRA_PRICE_BLOGS", "D"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run()
