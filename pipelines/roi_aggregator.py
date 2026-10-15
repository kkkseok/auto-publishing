"""
일일 ROI 집계 — 쿠팡 파트너스 리포트를 subId 기준으로 정확히 귀속한다.

이전 버전은 어제 수익 총합을 어제 발행 글 수로 나눠 키워드마다 똑같이
얹었다. 어느 글이 벌었는지 알 수 없는 값이라 측정으로서 의미가 없었다
(2026-10-15 코드 점검). 이 버전은 추측 분배를 하지 않는다.

귀속 규칙:
  1. 쿠팡 리포트는 subId 단위다. 발행 글의 subId 는 publish_queue 의
     affiliate_url 의 `subid` 파라미터에서 읽는다.
  2. subId 하나가 발행 글 하나에만 쓰였으면 → 그 글(url·keyword)에 귀속.
  3. 여러 글이 같은 subId 를 공유하면 → 채널(subId) 단위로만 기록하고 글/키워드
     에는 분배하지 않는다. 글 단위 ROI 를 원하면 파이프라인이 글마다 고유
     subId 를 써야 한다.

쿠팡 주문은 확정까지 늦게 반영되므로 매 실행마다 최근 ROI_LOOKBACK_DAYS(기본 7)일을
다시 조회해 그 날짜들을 덮어쓴다. 누적값은 일별 값에서 매번 다시 계산한다.

출력:
  data/coupang_roi.json — {"daily": {날짜: {subId: 통계+귀속}}, "by_sub", "by_post", "updated_at"}
  data/keyword_roi.json — {keyword: {clicks, orders, commission, publishes, first_seen, last}}
                          (2번 규칙으로 정확히 귀속된 글만. daily_summary·키워드 선택·대시보드가 읽음)

스케줄: SCHEDULE_ROI_AGGREGATE
"""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
load_dotenv()

from common.coupang_stats import fetch_daily_stats
from common.logger import log
from common.publish_queue import _load as _load_queue, DEFAULT_QUEUE_PATH


_BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUPANG_ROI  = os.path.join(_BASE_DIR, "data", "coupang_roi.json")
KEYWORD_ROI  = os.path.join(_BASE_DIR, "data", "keyword_roi.json")
_KEEP_DAYS   = 400
_METRICS     = ("clicks", "orders", "commission", "gmv")


SCHEDULE = {
    "env":  "SCHEDULE_ROI_AGGREGATE",
    "func": "run",
}


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _subid_of(affiliate_url: str) -> str:
    """affiliate_url 의 subid 파라미터 (쿠팡 링크는 `subid`, API 딥링크는 `subId`)."""
    if not affiliate_url:
        return ""
    qs = parse_qs(urlparse(affiliate_url).query)
    for key in ("subid", "subId"):
        if qs.get(key):
            return qs[key][0]
    return ""


def _index_posts_by_subid(queue: list) -> dict[str, list[dict]]:
    """subId → 그 subId 를 쓴 쿠팡 발행 글 목록."""
    idx: dict[str, list[dict]] = defaultdict(list)
    for it in queue:
        sid = _subid_of(it.get("affiliate_url") or "")
        if not sid or "coupang.com" not in (it.get("affiliate_url") or ""):
            continue
        idx[sid].append({
            "url":       it.get("url") or "",
            "platform":  it.get("platform") or "",
            "keyword":   (it.get("keyword") or "").strip(),
            "queued_at": it.get("queued_at") or "",
        })
    return idx


def _attribute(row: dict, posts: list[dict]) -> dict:
    """리포트 행 하나에 귀속 정보를 붙인다. 글이 하나일 때만 글에 귀속."""
    out: dict = {m: int(row.get(m) or 0) for m in _METRICS}
    out["platforms"] = sorted({p["platform"] for p in posts if p["platform"]})
    out["post_count"] = len(posts)
    if len(posts) == 1:
        out["post_url"] = posts[0]["url"]
        out["keyword"] = posts[0]["keyword"]
    return out


def _rebuild_totals(daily: dict) -> tuple[dict, dict, dict]:
    """일별 값에서 subId·글·키워드 누적을 다시 계산한다."""
    by_sub: dict = {}
    by_post: dict = {}
    by_kw: dict = {}
    for day in sorted(daily):
        for sid, d in daily[day].items():
            s = by_sub.setdefault(sid, {m: 0 for m in _METRICS} | {"platforms": []})
            for m in _METRICS:
                s[m] += d.get(m, 0)
            s["platforms"] = sorted(set(s["platforms"]) | set(d.get("platforms", [])))
            url = d.get("post_url")
            if not url:
                continue
            p = by_post.setdefault(url, {m: 0 for m in _METRICS} | {"keyword": d.get("keyword", ""), "sub_id": sid})
            for m in _METRICS:
                p[m] += d.get(m, 0)
            kw = d.get("keyword")
            if kw:
                k = by_kw.setdefault(kw, {"clicks": 0, "orders": 0, "commission": 0,
                                          "publishes": 1, "first_seen": day, "last": day})
                k["clicks"] += d.get("clicks", 0)
                k["orders"] += d.get("orders", 0)
                k["commission"] += d.get("commission", 0)
                k["last"] = day
    return by_sub, by_post, by_kw


def run() -> None:
    lookback = max(1, int(os.getenv("ROI_LOOKBACK_DAYS", "7") or "7"))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=lookback - 1)
    log(f"[ROI] 집계 시작 — {start} ~ {end} ({lookback}일 재조회)", "step")

    queue = _load_queue(DEFAULT_QUEUE_PATH)
    posts_by_sub = _index_posts_by_subid(queue)

    db = _load_json(COUPANG_ROI, {"daily": {}})
    daily: dict = db.get("daily", {})

    unattributed_subs: set[str] = set()
    shared_subs: set[str] = set()
    fetched_days = 0
    for i in range(lookback):
        day = start + timedelta(days=i)
        # None(조회 실패)과 [](활동 0건)을 구분한다. 실패를 warn 으로 삼키면
        # ledger 가 success 로 오판해 측정 루프 죽음이 몇 주씩 조용히 지나간다.
        rows = fetch_daily_stats(day, day)
        if rows is None:
            log(f"[ROI] {day} 쿠팡 리포트 조회 실패 (자격/서명/HTTP) — 이 날짜는 갱신 안 함. "
                "python -m common.coupang_stats 로 확인", "error")
            continue
        fetched_days += 1
        bucket = {}
        for r in rows:
            sid = r.get("subId") or "(none)"
            posts = posts_by_sub.get(sid, [])
            if not posts:
                unattributed_subs.add(sid)
            elif len(posts) > 1:
                shared_subs.add(sid)
            bucket[sid] = _attribute(r, posts)
        daily[day.isoformat()] = bucket

    if fetched_days == 0:
        log("[ROI] 모든 날짜 조회 실패 — 저장하지 않음", "error")
        return

    cutoff = (end - timedelta(days=_KEEP_DAYS)).isoformat()
    daily = {d: v for d, v in daily.items() if d >= cutoff}
    by_sub, by_post, by_kw = _rebuild_totals(daily)

    _save_json(COUPANG_ROI, {
        "daily": daily, "by_sub": by_sub, "by_post": by_post,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    _save_json(KEYWORD_ROI, by_kw)

    window = [daily[d] for d in daily if start.isoformat() <= d <= end.isoformat()]
    clicks = sum(v.get("clicks", 0) for b in window for v in b.values())
    comm = sum(v.get("commission", 0) for b in window for v in b.values())
    log(f"[ROI] 최근 {lookback}일: 클릭 {clicks}, 수수료 {comm:,}원 / "
        f"subId {len(by_sub)}개 누적, 글 단위 귀속 {len(by_post)}건", "ok")
    if shared_subs:
        log(f"[ROI] subId 공유로 글 단위 귀속 불가: {sorted(shared_subs)} — "
            "글마다 고유 subId 를 쓰면 글/키워드 ROI 가 잡힌다", "info")
    if unattributed_subs:
        log(f"[ROI] publish_queue 에 없는 subId: {sorted(unattributed_subs)}", "info")


if __name__ == "__main__":
    run()
