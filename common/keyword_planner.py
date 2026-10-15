"""블로그 유형별 글감 키워드 선정 — 검색량만이 아니라 "이길 수 있는 수요"를 고른다.

점수 = log(월 검색량) × 경쟁도 가중 × 의도 가중, 검색량이 너무 작거나 큰 키워드는 제외.
840건 실험에서 인기 키워드를 그대로 쓰면 새 블로그는 노출되지 않았다(글당 0.06클릭).
그래서 대형 키워드(월 수십만)는 빼고, 신청·가격·비교처럼 행동 직전 의도를 우대한다.

데이터: common/naver_searchad.py (네이버 검색광고 키워드 도구, 공식)
"""
import math
import re

from common.naver_searchad import related_keywords

# 유형별 씨앗 키워드 — 검색광고 API 가 연관 키워드로 넓혀 준다.
SEEDS = {
    "info": [
        ["근로장려금", "자녀장려금", "에너지바우처", "기초연금", "부모급여"],
        ["연말정산", "종합소득세", "건강보험료환급", "실업급여", "청년도약계좌"],
        ["전기요금", "가스요금할인", "통신비할인", "국민내일배움카드", "청년월세지원"],
        ["건강검진", "의료비공제", "실손보험청구", "자동차세연납", "재산세납부"],
    ],
    "price": [
        ["가습기", "공기청정기", "로봇청소기", "에어프라이어", "전기요"],
        ["무선이어폰", "태블릿", "노트북", "모니터", "스마트워치"],
        ["기저귀", "분유", "유모차", "카시트", "물티슈"],
        ["선크림", "샴푸", "전동칫솔", "단백질보충제", "비타민"],
        ["캠핑의자", "러닝화", "요가매트", "강아지사료", "고양이모래"],
    ],
}

# 행동 직전 의도 — 이런 말이 붙은 검색은 글을 끝까지 읽고 클릭할 가능성이 높다.
INTENT = {
    "info": ("신청", "방법", "대상", "조건", "기간", "금액", "지급일", "자격", "서류", "조회", "환급", "일정"),
    "price": ("추천", "가격", "최저가", "비교", "순위", "할인", "가성비", "후기"),
}
COMP_WEIGHT = {"낮음": 1.0, "중간": 0.6, "높음": 0.3}
VOLUME_RANGE = {"info": (300, 60000), "price": (300, 40000)}


def score(row: dict, kind: str) -> float:
    lo, hi = VOLUME_RANGE[kind]
    total = row["total"]
    if not lo <= total <= hi:
        return 0.0
    intent = 1.4 if any(w in row["keyword"] for w in INTENT[kind]) else 1.0
    return round(math.log10(total) * COMP_WEIGHT.get(row["comp"], 0.5) * intent, 3)


def plan(kind: str, used: set[str] | None = None, top: int = 30) -> list[dict]:
    """유형(info|price)별 상위 후보. used 에 있는 키워드는 뺀다."""
    used = {re.sub(r"\s+", "", u) for u in (used or set())}
    seen: dict[str, dict] = {}
    for seeds in SEEDS[kind]:
        rows = related_keywords(seeds) or []
        for r in rows:
            k = r["keyword"]
            if k in used or k in seen:
                continue
            # 연관 키워드엔 병원·지명·쇼핑몰처럼 주제와 무관한 것이 섞인다 — 씨앗을 포함해야 채택
            if not any(seed in k for seed in seeds):
                continue
            s = score(r, kind)
            if s > 0:
                seen[k] = dict(r, score=s)
    return sorted(seen.values(), key=lambda r: r["score"], reverse=True)[:top]
