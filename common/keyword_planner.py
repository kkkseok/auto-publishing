"""블로그 유형별 글감 키워드 선정 — 검색량만이 아니라 "이길 수 있는 수요"를 고른다.

점수 = log(월 검색량) × 경쟁도 가중 × 의도 가중, 검색량이 너무 작거나 큰 키워드는 제외.
840건 실험에서 인기 키워드를 그대로 쓰면 새 블로그는 노출되지 않았다(글당 0.06클릭).
그래서 대형 키워드(월 수십만)는 빼고, 신청·가격·비교처럼 행동 직전 의도를 우대한다.

데이터: common/naver_searchad.py (네이버 검색광고 키워드 도구, 공식)
"""
import math
import re
import time

from common.naver_searchad import related_keywords

# 카테고리별 씨앗 키워드 — 검색광고 API 가 연관 키워드로 넓혀 주고, 같은 목록으로 글의 카테고리를
# 고른다. 카테고리 이름은 블로그 관리의 카테고리 이름과 글자까지 같아야 발행 때 선택된다
# (네이버: Aside, 티스토리: 확장 content.js). 생활정보 A·C / 가격형 B·D (10-16 네 블로그에 생성).
# 씨앗은 구체적인 것만 둔다 — 같은 씨앗은 30일 안에 다시 쓰지 않으므로(blog_drafts_daily._pick)
# '강아지'처럼 넓은 말을 씨앗으로 두면 그 주제 전체가 한 달간 막힌다. 넓은 말은 ALIASES 에.
# 생활정보·가격형 각각 하루 2개를 쓰므로 씨앗이 60개 안팎이면 한 달을 버틴다.
INFO_CATEGORIES = {
    "지원금·복지": ("근로장려금", "자녀장려금", "에너지바우처", "기초연금", "부모급여", "아동수당", "첫만남이용권",
                 "청년월세지원", "주거급여", "긴급복지지원", "문화누리카드", "기초생활수급자", "출산지원금", "노인일자리"),
    "세금·환급": ("연말정산", "종합소득세", "의료비공제", "자동차세연납", "재산세", "건강보험료환급", "부가가치세",
               "양도소득세", "취득세", "증여세", "지방세", "세금환급"),
    "생활요금·할인": ("전기요금", "가스요금", "통신비할인", "알뜰교통카드", "K패스", "온누리상품권", "지역화폐",
                  "고속도로통행료", "수도요금", "난방비"),
    "취업·청년": ("실업급여", "청년도약계좌", "국민내일배움카드", "국민취업지원제도", "청년내일채움공제", "육아휴직급여",
               "최저임금", "주휴수당", "퇴직금", "청년전세대출", "주택청약"),
    "건강·보험": ("건강검진", "실손보험청구", "국민연금", "독감예방접종", "대상포진예방접종", "장기요양등급",
               "산정특례", "본인부담상한제"),
}

PRICE_CATEGORIES = {
    "생활가전": ("가습기", "공기청정기", "로봇청소기", "에어프라이어", "전기요", "제습기", "선풍기", "무선청소기",
             "식기세척기", "음식물처리기", "전기밥솥", "커피머신", "정수기", "의류건조기", "전자레인지"),
    "디지털": ("무선이어폰", "태블릿", "노트북", "모니터", "스마트워치", "기계식키보드", "게이밍마우스", "외장SSD",
            "보조배터리", "블루투스스피커", "사운드바", "웹캠", "액션캠"),
    "출산·육아": ("기저귀", "분유", "유모차", "카시트", "물티슈", "아기띠", "젖병소독기", "분유포트", "유아식탁의자",
              "아기로션"),
    "뷰티·건강": ("선크림", "샴푸", "전동칫솔", "단백질보충제", "비타민", "유산균", "헤어드라이어", "고데기",
              "전기면도기", "마사지건", "체중계", "혈압계"),
    "스포츠·레저": ("캠핑의자", "러닝화", "요가매트", "텐트", "침낭", "등산화", "아이스박스", "자전거", "폼롤러", "덤벨"),
    "반려동물": ("강아지사료", "고양이모래", "고양이사료", "강아지간식", "자동급식기", "펫드라이룸", "강아지패드", "캣타워"),
}

# 카테고리 고를 때만 쓰는 넓은 말 (씨앗 아님)
ALIASES = {
    "생활가전": ("청소기", "건조기"), "디지털": ("이어폰", "키보드", "마우스", "SSD"),
    "출산·육아": ("젖병", "아기"), "뷰티·건강": ("로션", "영양제"), "스포츠·레저": ("캠핑", "등산"),
    "반려동물": ("강아지", "고양이", "사료"), "건강·보험": ("보험",), "취업·청년": ("청년",),
}


def _chunks(seq: list[str], n: int = 5) -> list[list[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


# 검색광고 API 는 힌트를 한 번에 5개까지 받는다
SEEDS = {
    "info": _chunks([s for seeds in INFO_CATEGORIES.values() for s in seeds]),
    "price": _chunks([s for seeds in PRICE_CATEGORIES.values() for s in seeds]),
}


def _match(keyword: str, table: dict) -> str:
    k = keyword.replace(" ", "")
    hit = next((cat for cat, seeds in table.items() if any(s in k for s in seeds)), "")
    return hit or next((cat for cat in table if any(a in k for a in ALIASES.get(cat, ()))), "")


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
        time.sleep(0.3)   # 검색광고 API 연속 호출 간격
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
