"""매일 쿠팡 카테고리 베스트셀러 가격을 data/price_history.db 에 적재.

가격형 블로그(B·D)의 "30일 최저가 / 지금 사도 되나" 글은 이 데이터가 30일 이상
쌓여야 성립한다. 블로그를 열기 전부터 돌려 두는 이유다.

가격형 글에 실은 상품도 매일 기록한다: 글을 쓴 가격 키워드(data/blog_keywords_used.json,
kind=price)를 같은 검색 API 로 다시 조회해 상위 상품 가격을 `search:<키워드>` 로 남긴다.
검색 API 호출을 아끼려고 한 번에 PRICE_TRACK_MAX 개(기본 10)까지만, 오래 안 본 키워드부터.

카테고리 ID 는 파트너스 bestcategories 엔드포인트 기준(2026-10-15 실측). 같은 이름이
여러 ID 에 걸리는 것은 쿠팡 쪽 분류 그대로이고, 상품 행에는 API 가 준 categoryName
을 저장한다. PRICE_CATEGORIES 로 덮어쓸 수 있다.

수동 실행: python -m pipelines.price_collector
스케줄: SCHEDULE_PRICE_COLLECT
"""
import hashlib
import hmac
import json
import os
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from urllib import parse

import requests
from dotenv import load_dotenv
load_dotenv()

from common import price_history
from common.logger import log

SCHEDULE = {
    "env":  "SCHEDULE_PRICE_COLLECT",
    "func": "run",
}

# 1016 가전디지털 · 1013/1015/1017 생활용품 · 1020 주방 · 1010 뷰티 · 1011/1014 출산·유아
# 1008 스포츠·레저 · 1029 반려 · 1018 자동차 · 1021 문구
DEFAULT_CATEGORIES = "1016,1013,1015,1017,1020,1010,1011,1014,1008,1029,1018,1021"
_BASE = "https://api-gateway.coupang.com"
_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/bestcategories/{}"
_SEARCH = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
_ROOT = Path(__file__).resolve().parent.parent
USED = _ROOT / "data" / "blog_keywords_used.json"
TRACK_STATE = _ROOT / "data" / "price_track_state.json"


def _get(path: str, query: str, label: str) -> list | dict | None:
    """파트너스 API GET 의 data. 실패면 None (빈 목록과 구분)."""
    ak = os.getenv("COUPANG_ACCESS_KEY", "")
    sk = os.getenv("COUPANG_SECRET_KEY", "")
    if not ak or not sk:
        log("COUPANG_ACCESS_KEY/SECRET_KEY 미설정", "error")
        return None
    dt = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    sig = hmac.new(sk.encode(), (dt + "GET" + path + query).encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.get(f"{_BASE}{path}?{query}", timeout=20, headers={
            "Authorization": f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={dt}, signature={sig}",
            "Content-Type": "application/json;charset=UTF-8"})
    except requests.RequestException as e:
        log(f"[가격수집] {label} 요청 오류: {e}", "error")
        return None
    if not r.ok:
        log(f"[가격수집] {label} HTTP {r.status_code} {r.text[:120]}", "error")
        return None
    return r.json().get("data") or []


def _fetch(cat_id: str, limit: int = 100) -> list | None:
    """베스트카테고리 원본 item 목록. 실패면 None (빈 목록과 구분)."""
    return _get(_PATH.format(cat_id), f"limit={limit}", cat_id)


def _search(keyword: str) -> list | None:
    data = _get(_SEARCH, f"keyword={parse.quote(keyword)}&limit=10", keyword)
    return None if data is None else (data.get("productData") or [])


def tracked_keywords() -> list[str]:
    """가격형 글을 쓴 키워드 — 띄어쓰기만 다른 중복은 하나로."""
    try:
        used = json.loads(USED.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen, out = set(), []
    for k, v in used.items():
        key = k.replace(" ", "")
        if v.get("kind") == "price" and key not in seen:
            seen.add(key)
            out.append(k)
    return out


def track_post_keywords(conn, day: str) -> tuple[int, int]:
    """글에 실은 상품 가격 기록 — (저장 건수, 실패 키워드 수)."""
    try:
        state = json.loads(TRACK_STATE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    kws = sorted(tracked_keywords(), key=lambda k: state.get(k, ""))   # 오래 안 본 것부터
    cap = int(os.getenv("PRICE_TRACK_MAX", "10"))
    if len(kws) > cap:
        log(f"[가격수집] 추적 키워드 {len(kws)}개 중 {cap}개만 — PRICE_TRACK_MAX 또는 수집 횟수를 늘릴 것", "warn")
    saved = failed = 0
    for k in kws[:cap]:
        items = _search(k)
        if items is None:
            failed += 1
            continue
        saved += price_history.record(conn, day, f"search:{k}", items)
        state[k] = day
        time.sleep(1.5)
    TRACK_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return saved, failed


def run() -> None:
    cats = [c.strip() for c in os.getenv("PRICE_CATEGORIES", DEFAULT_CATEGORIES).split(",") if c.strip()]
    day = date.today().isoformat()
    log(f"[가격수집] {day} — 카테고리 {len(cats)}개", "step")
    saved = failed = 0
    with closing(price_history.connect()) as conn:
        for cat in cats:
            items = _fetch(cat)
            if items is None:
                failed += 1
            else:
                saved += price_history.record(conn, day, f"best:{cat}", items)
            time.sleep(1.5)   # 파트너스 API 호출 간격 — 짧은 연속 호출 회피
        t_saved, t_failed = track_post_keywords(conn, day)
    log(f"[가격수집] 글 키워드 추적 — {t_saved}건 저장, 실패 {t_failed}", "error" if t_failed else "ok")
    s = price_history.summary()
    if failed == len(cats):
        log(f"[가격수집] 전 카테고리 실패 — 저장 0건", "error")
        return
    log(f"[가격수집] 완료 — 오늘 {saved}건 저장 (실패 카테고리 {failed}) / 누적 상품 {s['products']}개, "
        f"{s['days']}일치", "ok" if not failed else "error")


if __name__ == "__main__":
    run()
