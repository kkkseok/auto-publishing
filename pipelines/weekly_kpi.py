"""
주간 KPI 리포트 — 재기획 판단용 숫자를 매주 한 번 텔레그램으로 보낸다.

일일 요약(daily_summary)이 "오늘 무슨 일이 있었나"라면, 이건 "이번 주에 무엇이
돈이 됐고 무엇이 안 됐나"다. 8주차 트랙 판단(설계 문서 6절)의 근거가 된다.

내용 (지난 7일, 어제까지):
  - 쿠팡: 클릭·주문·수수료와 전주 대비, 월 목표 대비 주간 페이스
  - 채널별 쿠팡 실적 (coupang_roi.json 의 subId → 발행 플랫폼)
  - 수익 상위 글 (글마다 고유 subId 를 쓴 글만 잡힌다)
  - 발행 수 (플랫폼별)
  - 파이프라인 성공/실패 (run ledger)

스케줄러는 매일 슬롯만 지원하므로 매일 돌되 KPI_WEEKDAY(기본 0=월요일)에만
보낸다. 수동 실행:
    python -m pipelines.weekly_kpi --force           # 요일 무시하고 전송
    python -m pipelines.weekly_kpi --force --dry-run # 전송 없이 출력만

수익 숫자는 pipelines.roi_aggregator 가 만든 data/coupang_roi.json 을 읽는다.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.logger import log
from common.notifier import _send_telegram

_BASE_DIR = Path(__file__).resolve().parent.parent
DATA = _BASE_DIR / "data"

SCHEDULE = {
    "env":  "SCHEDULE_WEEKLY_KPI",
    "func": "run",
}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _window(end: date, days: int = 7) -> tuple[str, str]:
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _coupang_totals(daily: dict, start: str, end: str) -> dict:
    tot = Counter()
    for day, subs in daily.items():
        if start <= day <= end:
            for d in subs.values():
                for m in ("clicks", "orders", "commission", "gmv"):
                    tot[m] += d.get(m, 0)
    return tot


def _coupang_by_platform(daily: dict, start: str, end: str) -> dict:
    out: dict = defaultdict(Counter)
    for day, subs in daily.items():
        if not (start <= day <= end):
            continue
        for d in subs.values():
            label = "+".join(d.get("platforms") or []) or "미확인"
            out[label]["clicks"] += d.get("clicks", 0)
            out[label]["commission"] += d.get("commission", 0)
    return out


def _top_posts(daily: dict, start: str, end: str, n: int = 3) -> list[tuple[str, Counter]]:
    posts: dict = defaultdict(Counter)
    for day, subs in daily.items():
        if not (start <= day <= end):
            continue
        for d in subs.values():
            url = d.get("post_url")
            if url:
                posts[url]["clicks"] += d.get("clicks", 0)
                posts[url]["commission"] += d.get("commission", 0)
    ranked = sorted(posts.items(), key=lambda kv: (kv[1]["commission"], kv[1]["clicks"]), reverse=True)
    return ranked[:n]


def _pct(cur: int, prev: int) -> str:
    if prev == 0:
        return "전주 0" if cur == 0 else "전주 0 → 신규"
    return f"{(cur - prev) / prev * 100:+.0f}%"


def build_report(end: date | None = None) -> str:
    end = end or (date.today() - timedelta(days=1))
    start, end_s = _window(end)
    pstart, pend = _window(end - timedelta(days=7))

    roi = _load(DATA / "coupang_roi.json", {})
    daily = roi.get("daily", {})
    queue = _load(DATA / "publish_queue.json", [])
    runs = _load(DATA / "pipeline_runs.json", [])

    cur = _coupang_totals(daily, start, end_s)
    prev = _coupang_totals(daily, pstart, pend)
    goal = int(os.getenv("KPI_MONTHLY_GOAL", "1000000") or 1000000)
    weekly_goal = goal * 7 // 30

    lines = [f"📈 주간 KPI — {start} ~ {end_s}"]
    if not daily:
        lines.append("• 쿠팡: 집계 데이터 없음 (roi_aggregator 실행 여부 확인)")
    else:
        lines.append(
            f"• 쿠팡: 클릭 {cur['clicks']} ({_pct(cur['clicks'], prev['clicks'])}) · "
            f"주문 {cur['orders']} · 수수료 {cur['commission']:,}원 "
            f"({_pct(cur['commission'], prev['commission'])})"
        )
        lines.append(f"  └ 목표 페이스: 주 {weekly_goal:,}원 중 "
                     f"{cur['commission'] / weekly_goal * 100:.1f}% (월 {goal:,}원 기준)")
        by_plat = _coupang_by_platform(daily, start, end_s)
        if by_plat:
            plat = ", ".join(f"{k} {v['clicks']}클릭/{v['commission']:,}원"
                             for k, v in sorted(by_plat.items(), key=lambda kv: -kv[1]["commission"]))
            lines.append(f"  └ 채널별: {plat}")
        top = _top_posts(daily, start, end_s)
        if top:
            lines.append("• 수익 상위 글:")
            for url, c in top:
                lines.append(f"  {c['commission']:,}원 / {c['clicks']}클릭 — {url}")
        else:
            lines.append("• 글 단위 귀속 없음 — 글마다 고유 subId(post_subid)를 써야 잡힘")

    pubs = Counter(it.get("platform") or "?" for it in queue
                   if start <= (it.get("queued_at") or "")[:10] <= end_s)
    if pubs:
        lines.append("• 발행: " + ", ".join(f"{k} {v}" for k, v in pubs.most_common())
                     + f" (총 {sum(pubs.values())})")
    else:
        lines.append("• 발행: 0건")

    status = defaultdict(Counter)
    for r in runs:
        if start <= (r.get("started_at") or "")[:10] <= end_s:
            status[(r.get("module") or "?").replace("pipelines.", "")][r.get("status") or "?"] += 1
    bad = {m: c for m, c in status.items() if c.get("failure")}
    if status:
        ok_runs = sum(c.get("success", 0) for c in status.values())
        fail_runs = sum(c.get("failure", 0) for c in status.values())
        lines.append(f"• 실행: 성공 {ok_runs} · 실패 {fail_runs}")
        for m, c in sorted(bad.items(), key=lambda kv: -kv[1]["failure"]):
            lines.append(f"  └ {m}: 실패 {c['failure']}/{sum(c.values())}")
    return "\n".join(lines)


def run(force: bool = False, dry_run: bool = False) -> None:
    weekday = int(os.getenv("KPI_WEEKDAY", "0") or 0)
    if not force and date.today().weekday() != weekday:
        log(f"[KPI] 오늘은 전송 요일 아님 (KPI_WEEKDAY={weekday}) — skip", "info")
        return
    text = build_report()
    if dry_run:
        print(text)
        return
    _send_telegram(text)
    log("[KPI] 주간 리포트 전송 완료", "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run(force="--force" in sys.argv, dry_run="--dry-run" in sys.argv)
