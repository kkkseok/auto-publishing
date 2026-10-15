"""매일 쿠팡 카테고리 베스트셀러 가격을 data/price_history.db 에 적재.

가격형 블로그(B·D)의 "30일 최저가 / 지금 사도 되나" 글은 이 데이터가 30일 이상
쌓여야 성립한다. 블로그를 열기 전부터 돌려 두는 이유다.

카테고리 ID 는 파트너스 bestcategories 엔드포인트 기준(2026-10-15 실측). 같은 이름이
여러 ID 에 걸리는 것은 쿠팡 쪽 분류 그대로이고, 상품 행에는 API 가 준 categoryName
을 저장한다. PRICE_CATEGORIES 로 덮어쓸 수 있다.

수동 실행: python -m pipelines.price_collector
스케줄: SCHEDULE_PRICE_COLLECT
"""
import hashlib
import hmac
import os
import time
from contextlib import closing
from datetime import date, datetime, timezone

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


def _fetch(cat_id: str, limit: int = 100) -> list | None:
    """베스트카테고리 원본 item 목록. 실패면 None (빈 목록과 구분)."""
    ak = os.getenv("COUPANG_ACCESS_KEY", "")
    sk = os.getenv("COUPANG_SECRET_KEY", "")
    if not ak or not sk:
        log("COUPANG_ACCESS_KEY/SECRET_KEY 미설정", "error")
        return None
    path = _PATH.format(cat_id)
    query = f"limit={limit}"
    dt = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    sig = hmac.new(sk.encode(), (dt + "GET" + path + query).encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.get(f"{_BASE}{path}?{query}", timeout=20, headers={
            "Authorization": f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={dt}, signature={sig}",
            "Content-Type": "application/json;charset=UTF-8"})
    except requests.RequestException as e:
        log(f"[가격수집] {cat_id} 요청 오류: {e}", "error")
        return None
    if not r.ok:
        log(f"[가격수집] {cat_id} HTTP {r.status_code} {r.text[:120]}", "error")
        return None
    return r.json().get("data") or []


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
    s = price_history.summary()
    if failed == len(cats):
        log(f"[가격수집] 전 카테고리 실패 — 저장 0건", "error")
        return
    log(f"[가격수집] 완료 — 오늘 {saved}건 저장 (실패 카테고리 {failed}) / 누적 상품 {s['products']}개, "
        f"{s['days']}일치", "ok" if not failed else "error")


if __name__ == "__main__":
    run()
