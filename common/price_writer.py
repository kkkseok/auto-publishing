"""가격형(B·D) 글 재료 — 쿠팡 상품 데이터 + 구매 기준 조사.

가격·링크는 모델에 맡기지 않는다. 쿠팡 파트너스 검색 API 로 받은 상품(이름·현재가·로켓)을
코드가 표로 넣고(draft_writer._products_block), 모델은 그 데이터와 조사한 구매 기준으로
설명·선택 기준·FAQ 를 쓴다. 가격 이력(common/price_history)이 30일 쌓이기 전에는
"가격 추이·역대 최저가" 를 쓰지 못하게 하고 구매 가이드형으로 쓴다.

링크의 subId 는 블로그마다 따로(post_subid: nb=네이버 B, ts=티스토리 D) — 글 단위 ROI 귀속용.
"""
from contextlib import closing
from datetime import datetime

from common import price_history
from common.draft_writer import research
from sources.coupang import _api_search, _get_page_key, make_partners_link, post_subid

CHANNEL_CODE = {"B": "nb", "D": "ts"}
TREND_MIN_DAYS = 30

_PRICE_RESEARCH_SYSTEM = """너는 한국 쇼핑 정보 블로그의 자료 조사원이다.
주어진 상품 카테고리를 살 때 무엇을 보고 골라야 하는지 '구매 기준 시트'를 만든다.

규칙:
- 한국소비자원(소비자24·비교공감) 시험 결과, 정부 안전·에너지 인증 기준, 제조사 공식 사양 페이지를 1순위로 쓴다.
  언론 기사는 보조. 블로그·카페·쇼핑몰 후기는 근거로 쓰지 않는다.
- 가격은 조사하지 않는다 (가격은 따로 수집한 데이터를 쓴다).
- 모든 기준·수치 옆에 출처 URL 을 붙인다. 찾지 못한 항목은 '확인 안 됨'.
- 고를 때 볼 사양(용량·소비전력·소음·인증 등), 용도별 추천 기준, 관리·안전 주의점, 흔한 질문을 정리한다."""


def _product_id(url: str) -> int | None:
    key = _get_page_key(url)   # /products/<id> 또는 API 딥링크의 pageKey
    return int(key) if key.isdigit() and key != "0" else None


def fetch_products(keyword: str, count: int = 8) -> list[dict]:
    """쿠팡 검색 상위 상품 + 가격 이력 통계. affiliate_url 은 블로그별로 for_blog() 에서 붙인다."""
    products = _api_search(keyword, count)
    when = datetime.now().strftime("%Y.%m.%d %H:%M")
    with closing(price_history.connect()) as conn:
        for p in products:
            p["fetched_at"] = when
            pid = _product_id(p["url"])
            st = price_history.stats(conn, pid) if pid else None
            if st:
                st.pop("series", None)
            p["history"] = st
    return products


def for_blog(products: list[dict], blog: str) -> list[dict]:
    sub = post_subid(CHANNEL_CODE[blog])
    return [dict(p, affiliate_url=make_partners_link(p["url"], channel_id=sub), subid=sub) for p in products]


def facts(keyword: str, products: list[dict]) -> dict:
    """write() 에 넘길 사실 시트 — 구매 기준 조사 + 상품 데이터."""
    base = research(keyword, system=_PRICE_RESEARCH_SYSTEM)
    days = max((p["history"] or {}).get("days_observed", 0) for p in products) if products else 0
    lines = []
    for i, p in enumerate(products, 1):
        h = p["history"]
        hist = (f" · 기록 {h['days_observed']}일 최저 {h['low']:,}원 평균 {h['avg']:,}원" if h else " · 가격 기록 없음")
        lines.append(f"{i}. {p['name']} — 현재가 {p['price']}{' · 로켓배송' if p['is_rocket'] else ''}{hist}")
    data = (f"\n\n=== 쿠팡 검색 상위 상품 ({products[0]['fetched_at'] if products else '-'} 조회) ===\n"
            + "\n".join(lines) +
            "\n\n=== 쓰는 규칙 ===\n"
            "- 상품명·현재가·링크가 든 '가격 비교표'는 시스템이 본문 뒤에 따로 넣는다. 본문에 같은 표를 만들지 말고 "
            "'아래 가격표'라고 가리킨다. 표는 사양·선택 기준 비교로 쓴다.\n"
            "- 가격 숫자는 위 데이터에 있는 것만 쓴다. 할인율·카드혜택·쿠폰은 데이터에 없으니 쓰지 않는다.\n"
            f"- 가격 기록이 최대 {days}일이다. {TREND_MIN_DAYS}일 미만이면 '가격 추이', '역대 최저가', "
            "'지금이 살 타이밍' 같은 판단을 쓰지 말고 '구매 가이드'(고르는 기준·용도별 추천)로 쓴다.\n"
            "- 상품 사양은 구매 기준 시트에 근거가 있을 때만 쓴다. 상품을 직접 써본 것처럼 쓰지 않는다.\n"
            "- 쿠팡 파트너스 고지는 시스템이 맨 위에 넣는다. 본문에 따로 쓰지 않는다.\n"
            "- 확인되지 않은 항목은 본문에서 뺀다. '확인되지 않은 항목' 같은 소제목을 만들지 않는다.\n"
            "- 제목에는 키워드를 띄어쓰기를 살린 자연스러운 말로 쓴다 (예: '에어프라이어추천' → '에어프라이어 추천').")
    return dict(base, facts=base["facts"] + data, trend_days=days)
