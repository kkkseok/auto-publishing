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

# 블로그 카테고리 — 키워드에 든 씨앗으로 고른다. 이름은 블로그 관리의 카테고리 이름과
# 글자까지 같아야 발행 때 선택된다 (네이버: Aside, 티스토리: 확장 content.js).
# 생활정보형 A·C 는 INFO_CATEGORIES, 가격형 B·D 는 PRICE_CATEGORIES (10-16 네 블로그에 생성).
INFO_CATEGORIES = {
    "지원금·복지": ("근로장려금", "자녀장려금", "에너지바우처", "기초연금", "부모급여", "청년월세"),
    "세금·환급": ("연말정산", "종합소득세", "의료비공제", "자동차세", "재산세", "건강보험료환급"),
    "생활요금·할인": ("전기요금", "가스요금", "통신비"),
    "취업·청년": ("실업급여", "청년도약계좌", "내일배움카드"),
    "건강·보험": ("건강검진", "실손보험"),
}


PRICE_CATEGORIES = {
    "생활가전": ("가습기", "공기청정기", "로봇청소기", "에어프라이어", "전기요", "제습기", "청소기", "선풍기"),
    "디지털": ("무선이어폰", "이어폰", "태블릿", "노트북", "모니터", "스마트워치", "키보드", "마우스"),
    "출산·육아": ("기저귀", "분유", "유모차", "카시트", "물티슈", "젖병"),
    "뷰티·건강": ("선크림", "샴푸", "전동칫솔", "단백질보충제", "비타민", "유산균", "로션"),
    "스포츠·레저": ("캠핑의자", "캠핑", "러닝화", "요가매트", "텐트", "자전거"),
    "반려동물": ("강아지사료", "고양이모래", "강아지", "고양이", "사료"),
}


def _match(keyword: str, table: dict) -> str:
    k = keyword.replace(" ", "")
    return next((cat for cat, seeds in table.items() if any(s in k for s in seeds)), "")


def info_category(keyword: str) -> str:
    return _match(keyword, INFO_CATEGORIES)


# 티스토리 홈주제(발행 창 '홈주제' 목록의 이름 그대로). 쇼핑 항목이 없어 가장 가까운 것으로.
HOME_SUBJECT = {
    "지원금·복지": "생활정보", "세금·환급": "경제", "생활요금·할인": "생활정보",
    "취업·청년": "경영·직장", "건강·보험": "건강",
    "생활가전": "생활정보", "디지털": "IT 제품리뷰", "출산·육아": "육아",
    "뷰티·건강": "패션·뷰티", "스포츠·레저": "스포츠일반", "반려동물": "반려동물",
}


def home_subject_for(blog: str, keyword: str) -> str:
    """티스토리(C·D) 글의 홈주제. 카테고리를 못 고르면 '생활정보'."""
    return HOME_SUBJECT.get(category_for(blog, keyword), "생활정보")


def category_for(blog: str, keyword: str) -> str:
    """블로그(A·B·C·D)와 키워드로 카테고리 이름. 못 고르면 ''(카테고리 없음)."""
    return _match(keyword, PRICE_CATEGORIES if blog in ("B", "D") else INFO_CATEGORIES)


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


def seed_of(keyword: str, kind: str) -> str:
    """키워드가 속한 씨앗(예: '에너지바우처대상' → '에너지바우처'). 없으면 ''."""
    k = re.sub(r"\s+", "", keyword)
    return next((seed for group in SEEDS[kind] for seed in group if seed in k), "")


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
