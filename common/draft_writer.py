"""블로그 초안 생성 — 조사(웹 검색) → 작성(구조화 출력) → HTML 렌더링.

두 단계로 나눈 이유: 웹 검색 결과의 인용(citations)과 JSON 구조화 출력은 한 요청에서
함께 쓸 수 없다. 1단계는 공식 출처 위주로 사실을 모아 출처 URL 과 함께 정리하고,
2단계는 그 사실만 써서 prompts/style_guide_v1.md 의 블로그별 양식대로 JSON 을 만든다.
숫자·날짜는 1단계 사실 시트에 있는 것만 쓰게 해 지어내기를 막는다.

블로그 코드: A 네이버 생활정보 · B 네이버 가격형 · C 티스토리 생활정보 · D 티스토리 가격형
"""
import html
import json
import re
from datetime import date
from pathlib import Path

import anthropic

MODEL = "claude-opus-5-5"
_ROOT = Path(__file__).resolve().parent.parent
STYLE_GUIDE = (_ROOT / "prompts" / "style_guide_v1.md").read_text(encoding="utf-8")

BLOGS = {
    "A": "블로그 A: Naver · 생활정보·정책",
    "B": "블로그 B: Naver · 가격 데이터형 쇼핑",
    "C": "블로그 C: Tistory · 구글 검색용 생활정보·정책",
    "D": "블로그 D: Tistory · 구글 검색용 가격 데이터형 쇼핑",
}

_RESEARCH_SYSTEM = """너는 한국 생활 정보 블로그의 자료 조사원이다.
주어진 키워드에 대해 웹 검색으로 사실을 모아 '사실 시트'를 만든다.

규칙:
- 정부·공공기관(.go.kr, .or.kr, 국세청·복지로·정부24 등) 공식 페이지를 1순위로 쓴다. 언론 기사는 보조.
- 블로그·카페 글은 사실의 근거로 쓰지 않는다.
- 모든 숫자·날짜·조건 옆에 출처 URL 을 붙인다. 찾지 못한 항목은 '확인 안 됨'이라고 적는다.
- 올해 기준(귀속연도·신청연도)을 분명히 한다. 공고 전이면 '아직 발표 전'으로 적는다.
- 마지막에 '신청·조회 화면 URL' 목록(실제 신청/조회 페이지)을 따로 적는다 — 화면 캡처에 쓴다."""

_WRITE_SYSTEM = """너는 블로그 글 작성자다. 아래 문체·양식 가이드의 지정된 블로그 섹션과 공통 원칙을 따른다.
제공된 사실 시트에 있는 숫자·날짜·조건만 쓴다. 사실 시트에 없는 수치는 만들지 않는다.
개인 경험(써봤다, 방문했다)을 지어내지 않는다. 상위 블로그 문장을 흉내 내지 말고 새로 쓴다.

=== 문체·양식 가이드 ===
""" + STYLE_GUIDE

_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "meta_description", "basis_line", "intro", "summary_points", "sections",
                 "faq", "closing", "tags", "sources", "capture_urls"],
    "properties": {
        "title": {"type": "string"},
        "meta_description": {"type": "string"},
        "basis_line": {"type": "string", "description": "기준일·출처 한 줄"},
        "intro": {"type": "string"},
        "summary_points": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["heading", "paragraphs", "table_columns", "table_rows", "steps", "checklist"],
            "properties": {
                "heading": {"type": "string"},
                "paragraphs": {"type": "array", "items": {"type": "string"}},
                "table_columns": {"type": "array", "items": {"type": "string"}},
                "table_rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "checklist": {"type": "array", "items": {"type": "string"}},
            }}},
        "faq": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["q", "a"],
            "properties": {"q": {"type": "string"}, "a": {"type": "string"}}}},
        "closing": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "url"],
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}},
        "capture_urls": {"type": "array", "items": {"type": "string"},
                         "description": "본문 신청 단계에 넣을 공식 화면 URL (최대 4개)"},
    },
}


class DraftError(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _check(resp) -> None:
    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
        raise DraftError(f"모델이 요청을 거절함 (category={cat})")
    if resp.stop_reason == "max_tokens":
        raise DraftError("max_tokens 에 걸려 응답이 잘림")


def research(keyword: str, client: anthropic.Anthropic | None = None) -> dict:
    """키워드 사실 시트(텍스트)와 검색으로 확인된 출처 URL 목록."""
    client = client or _client()
    today = date.today().isoformat()
    with client.messages.stream(
        model=MODEL, max_tokens=32000,
        system=_RESEARCH_SYSTEM,
        output_config={"effort": "medium"},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 8,
                "user_location": {"type": "approximate", "country": "KR"}}],
        messages=[{"role": "user", "content": f"오늘은 {today}. 키워드: {keyword}\n사실 시트를 만들어 줘."}],
    ) as stream:
        resp = stream.get_final_message()
    _check(resp)
    text, urls = [], []
    for block in resp.content:
        if block.type == "text":
            text.append(block.text)
            for c in getattr(block, "citations", None) or []:
                u = getattr(c, "url", None)
                if u and u not in urls:
                    urls.append(u)
    facts_text = "".join(text)
    # 동적 필터링 웹 검색은 인용을 블록 대신 본문 URL 로만 남기기도 한다 — 둘 다 모은다
    for u in re.findall(r"https?://[^\s)\]>\"']+", facts_text):
        u = u.rstrip(".,")
        if u not in urls:
            urls.append(u)
    return {"keyword": keyword, "facts": facts_text, "cited_urls": urls,
            "usage": resp.usage.to_dict()}


def write(keyword: str, blog: str, facts: dict, client: anthropic.Anthropic | None = None) -> dict:
    """사실 시트 → 블로그 양식 JSON 초안."""
    client = client or _client()
    prompt = (f"작성할 블로그: {BLOGS[blog]} (가이드의 해당 섹션 양식을 따른다)\n"
              f"핵심 키워드: {keyword}\n오늘: {date.today().isoformat()}\n\n"
              f"=== 사실 시트 ===\n{facts['facts']}\n\n"
              f"=== 검색으로 확인된 출처 URL ===\n" + "\n".join(facts["cited_urls"]))
    with client.messages.stream(
        model=MODEL, max_tokens=32000,
        system=[{"type": "text", "text": _WRITE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": _DRAFT_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        resp = stream.get_final_message()
    _check(resp)
    raw = next(b.text for b in resp.content if b.type == "text")
    draft = json.loads(raw)
    draft["_usage"] = resp.usage.to_dict()
    return draft


def _e(s: str) -> str:
    return html.escape(s, quote=False)


def render_html(draft: dict, images: dict[str, str] | None = None) -> str:
    """초안 JSON → 블로그 본문 HTML (티스토리 발행·네이버 붙여넣기 공용).

    images: {capture_url: <img src>} — 신청 단계 섹션 아래에 붙인다.
    """
    images = images or {}
    out = [f'<p style="color:#666;font-size:14px">{_e(draft["basis_line"])}</p>',
           f"<p>{_e(draft['intro'])}</p>",
           '<div style="border:2px solid #1f8f7a;border-radius:10px;padding:14px 18px;margin:16px 0">'
           "<p><b>📌 3줄 요약</b></p><ul>" + "".join(f"<li>{_e(p)}</li>" for p in draft["summary_points"])
           + "</ul></div>"]
    pending_images = list(images.items())
    for sec in draft["sections"]:
        out.append(f"<h2>{_e(sec['heading'])}</h2>")
        out += [f"<p>{_e(p)}</p>" for p in sec["paragraphs"]]
        if sec["table_columns"] and sec["table_rows"]:
            head = "".join(f'<th style="border:1px solid #ccc;padding:6px 10px;background:#f5f5f5">{_e(c)}</th>'
                           for c in sec["table_columns"])
            body = "".join("<tr>" + "".join(f'<td style="border:1px solid #ccc;padding:6px 10px">{_e(c)}</td>'
                                             for c in row) + "</tr>" for row in sec["table_rows"])
            out.append(f'<table style="border-collapse:collapse;width:100%"><thead><tr>{head}</tr></thead>'
                       f"<tbody>{body}</tbody></table>")
        if sec["steps"]:
            out.append("<ol>" + "".join(f"<li>{_e(s)}</li>" for s in sec["steps"]) + "</ol>")
            while pending_images:
                url, src = pending_images.pop(0)
                out.append(f'<p><img src="{html.escape(src)}" alt="공식 화면: {html.escape(url)}" '
                           f'style="max-width:100%;border:1px solid #ddd"></p>')
        if sec["checklist"]:
            out.append("<ul>" + "".join(f"<li>✔ {_e(c)}</li>" for c in sec["checklist"]) + "</ul>")
    if draft["faq"]:
        out.append("<h2>자주 묻는 질문</h2>")
        for f in draft["faq"]:
            out.append(f"<p><b>Q. {_e(f['q'])}</b><br>A. {_e(f['a'])}</p>")
    out.append(f"<p>{_e(draft['closing'])}</p>")
    if draft["sources"]:
        out.append("<p><b>참고 출처</b></p><ul>" + "".join(
            f'<li><a href="{html.escape(s["url"])}">{_e(s["title"])}</a></li>' for s in draft["sources"]) + "</ul>")
    return "\n".join(out)
