"""티스토리 필수 안내 페이지(소개·개인정보처리방침·문의) HTML 생성 — 애드센스 심사 준비.

애드센스 자격 요건 자체에는 특정 페이지가 없지만, 광고를 게재하면 쿠키 사용을 알리는
개인정보처리방침이 필요하고, 소개·문의는 운영 주체와 연락 경로를 보여 줘 신뢰도를 높인다.
글은 사실만 쓴다 — 운영 방식(공식 자료 기반, AI 도구 활용, 쿠팡 파트너스)을 숨기지 않는다.

    .venv/Scripts/python -m tools.blog_pages      # data/pages/<blog>/*.html 생성
"""
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EFFECTIVE = "2026년 10월 16일"

BLOGS = {
    "kkkseok": dict(
        name="오늘의 생활 치트키", color="#3a4fd6",
        topic="지원금·세금·생활요금·취업·건강처럼 생활에 바로 쓰이는 제도와 신청 방법",
        categories=["지원금·복지", "세금·환급", "생활요금·할인", "취업·청년", "건강·보험"],
        sources="복지로·정부24·국세청 홈택스 등 공공기관의 공식 안내와 보도자료",
        affiliate=False),
    "pricerader": dict(
        name="프라이스 레이더", color="#6a35e0",
        topic="생활가전·디지털·육아·뷰티·스포츠·반려용품을 고를 때 볼 기준과 쿠팡 인기 상품의 가격",
        categories=["생활가전", "디지털", "출산·육아", "뷰티·건강", "스포츠·레저", "반려동물"],
        sources="한국소비자원 비교시험, 정부 인증 기준, 제조사 공식 사양, 쿠팡 파트너스 상품 데이터",
        affiliate=True),
}

_KEEP = "word-break:keep-all;line-height:1.85;font-size:16px;"


def _wrap(blog: dict, body: str) -> str:
    return f'<div style="{_KEEP}">{body}</div>'


def _h(text: str, color: str) -> str:
    return f'<h2 style="margin:30px 0 12px;font-size:21px;color:{color}">{html.escape(text)}</h2>'


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def about(slug: str, b: dict) -> tuple[str, str]:
    cats = " · ".join(b["categories"])
    aff = (_h("제휴 안내", b["color"]) +
           _p("이 블로그는 <b>쿠팡 파트너스 활동</b>에 참여하고 있습니다. 글 속 상품 링크를 통해 구매가 이루어지면 "
              "일정액의 수수료를 제공받으며, 해당 글 맨 위에 이 사실을 표시합니다. 수수료는 상품 선정이나 "
              "설명 내용에 영향을 주지 않으며, 가격은 쿠팡 파트너스 데이터를 조회한 시점 기준으로 표기합니다.")
           ) if b["affiliate"] else ""
    body = (
        _p(f"<b>{html.escape(b['name'])}</b>는 {html.escape(b['topic'])}을 한 화면에 정리하는 블로그입니다.") +
        _h("다루는 주제", b["color"]) + _p(html.escape(cats)) +
        _h("글을 만드는 방식", b["color"]) +
        _p(f"모든 글은 {html.escape(b['sources'])}를 바탕으로 작성합니다. 자료 조사와 초안 정리에는 AI 도구를 활용하며, "
           "공식 자료로 확인되지 않은 숫자나 조건은 쓰지 않습니다. 글마다 기준일과 참고 출처를 함께 적습니다.") +
        _p("제도와 가격은 수시로 바뀝니다. 신청·구매 전에는 글에 연결된 공식 안내나 판매 페이지에서 최신 내용을 "
           "한 번 더 확인해 주세요. 이 블로그의 글은 일반 정보 안내이며, 개별 자격 판단이나 구매 결정을 대신하지 않습니다.") +
        aff +
        _h("오류 제보", b["color"]) +
        _p(f'틀린 내용이나 바뀐 기준을 발견하시면 <a href="https://{slug}.tistory.com/guestbook">방명록</a>이나 '
           "해당 글의 댓글로 알려 주세요. 확인 후 바로 수정하고 수정일을 표시합니다.")
    )
    return "블로그 소개", _wrap(b, body)


def privacy(slug: str, b: dict) -> tuple[str, str]:
    c = b["color"]
    aff = (_h("4. 제휴 링크", c) +
           _p("글 속 쿠팡 상품 링크를 누르면 쿠팡으로 이동하며, 쿠팡은 구매 실적 확인을 위해 자체 쿠키를 사용할 수 "
              "있습니다. 쿠팡에서 처리하는 정보는 쿠팡의 개인정보처리방침을 따릅니다.")) if b["affiliate"] else ""
    n = 5 if b["affiliate"] else 4
    body = (
        _p(f"{html.escape(b['name'])}(https://{slug}.tistory.com, 이하 '블로그')는 방문자의 개인정보를 소중히 다루며, "
           "관련 법령을 지킵니다. 이 방침은 블로그가 어떤 정보를 어떻게 다루는지 안내합니다.") +
        _h("1. 블로그가 직접 수집하는 정보", c) +
        _p("블로그는 회원가입이나 별도 양식을 통해 개인정보를 직접 수집하지 않습니다. 댓글·방명록은 티스토리(카카오)가 "
           "제공하는 기능으로, 작성 시 처리되는 정보는 카카오의 개인정보처리방침을 따릅니다.") +
        _h("2. 방문 통계", c) +
        _p("블로그 운영 플랫폼인 티스토리는 방문 수·유입 경로 같은 통계를 제공합니다. 이 통계는 개인을 식별하지 않는 "
           "집계 정보이며, 콘텐츠 개선에만 사용합니다.") +
        _h("3. 광고와 쿠키", c) +
        _p("블로그에는 Google 애드센스를 비롯한 제3자 광고가 게재될 수 있습니다. Google 등 광고 사업자는 쿠키를 사용해 "
           "방문자가 이 블로그나 다른 웹사이트를 방문한 기록을 바탕으로 광고를 보여 줄 수 있습니다.") +
        _p('방문자는 <a href="https://adssettings.google.com">Google 광고 설정</a>에서 맞춤 광고를 끌 수 있으며, '
           "브라우저 설정에서 쿠키 저장을 거부할 수 있습니다. Google이 광고 쿠키를 사용하는 방식은 "
           '<a href="https://policies.google.com/technologies/ads">Google 광고 정책</a>에서 확인할 수 있습니다.') +
        aff +
        _h(f"{n}. 문의", c) +
        _p(f'개인정보와 관련한 문의는 <a href="https://{slug}.tistory.com/guestbook">방명록</a>에 남겨 주세요.') +
        _h(f"{n + 1}. 방침의 변경", c) +
        _p(f"이 방침은 {EFFECTIVE}부터 적용됩니다. 내용이 바뀌면 이 페이지에 변경일과 함께 알립니다.")
    )
    return "개인정보처리방침", _wrap(b, body)


def contact(slug: str, b: dict) -> tuple[str, str]:
    c = b["color"]
    body = (
        _p(f"{html.escape(b['name'])}에 대한 문의·오류 제보·제휴 제안은 아래 방법으로 받습니다.") +
        _h("방명록", c) +
        _p(f'<a href="https://{slug}.tistory.com/guestbook">https://{slug}.tistory.com/guestbook</a> 에 글을 남겨 주세요. '
           "비공개(비밀글)로 남기시면 운영자만 볼 수 있습니다.") +
        _h("글 댓글", c) +
        _p("특정 글의 내용에 관한 질문이나 오류 제보는 해당 글의 댓글로 남겨 주시면 더 빠르게 확인할 수 있습니다.") +
        _h("답변 안내", c) +
        _p("확인한 문의에는 보통 2~3일 안에 답변합니다. 오류 제보는 확인 후 글을 바로 고치고 수정일을 표시합니다.")
    )
    return "문의하기", _wrap(b, body)


def build(out_root: Path = ROOT / "data" / "pages") -> list[Path]:
    paths = []
    for slug, b in BLOGS.items():
        d = out_root / slug
        d.mkdir(parents=True, exist_ok=True)
        for key, fn in (("about", about), ("privacy", privacy), ("contact", contact)):
            title, body = fn(slug, b)
            p = d / f"{key}.html"
            p.write_text(f"<!-- title: {title} -->\n{body}", encoding="utf-8")
            paths.append(p)
    return paths


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    for p in build():
        print(p)
