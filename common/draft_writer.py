"""블로그 초안 생성 — 조사(웹 검색) → 작성(구조화 출력) → HTML 렌더링.

두 단계로 나눈 이유: 웹 검색 결과의 인용(citations)과 JSON 구조화 출력은 한 요청에서
함께 쓸 수 없다. 1단계는 공식 출처 위주로 사실을 모아 출처 URL 과 함께 정리하고,
2단계는 그 사실만 써서 prompts/style_guide_v1.md 의 블로그별 양식대로 JSON 을 만든다.
숫자·날짜는 1단계 사실 시트에 있는 것만 쓰게 해 지어내기를 막는다.

블로그 코드: A 네이버 생활정보 · B 네이버 가격형 · C 티스토리 생활정보 · D 티스토리 가격형
"""
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import anthropic

# 조사는 검색 결과를 많이 읽어 입력 토큰이 크다(1회 ~10만) → Sonnet 5.5 로 비용을 낮추고,
# 글 품질을 좌우하는 작성만 Opus 5.5 로 한다. 같은 주제의 A·C(또는 B·D) 글은
# 조사 1회를 공유한다 (2026-10-15 비용 절감안, 사용자 승인).
RESEARCH_MODEL = "claude-sonnet-5-5"
WRITE_MODEL = "claude-opus-5-5"
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

가독성 규칙 (모바일에서 읽힌다):
- paragraphs 의 각 항목은 1~2문장. 한 항목이 화면에서 4줄을 넘지 않게 짧게 끊는다.
- 핵심 숫자·기한·조건은 **이렇게** 감싸 강조한다. 한 문단에 최대 2곳.
- 섹션마다 독자가 놓치기 쉬운 점이 있으면 callout 에 넣는다 (tip=도움말, warning=주의). 없으면 kind 를 none.
- 같은 내용을 문단과 표에 중복해 쓰지 않는다.

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
            "required": ["heading", "paragraphs", "table_columns", "table_rows", "steps", "checklist",
                         "callout_kind", "callout_text"],
            "properties": {
                "heading": {"type": "string"},
                "paragraphs": {"type": "array", "items": {"type": "string"}},
                "table_columns": {"type": "array", "items": {"type": "string"}},
                "table_rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "steps": {"type": "array", "items": {"type": "string"}},
                "checklist": {"type": "array", "items": {"type": "string"}},
                "callout_kind": {"type": "string", "enum": ["tip", "warning", "none"]},
                "callout_text": {"type": "string"},
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


def _billing() -> str:
    """subscription (기본) = Claude Code CLI 로 구독 플랜 한도에서 실행, api = ANTHROPIC_API_KEY 과금."""
    return os.getenv("AI_BILLING", "subscription").strip().lower()


def _run_cli(prompt: str, system: str, model: str, tools: list[str],
             schema: dict | None = None, timeout: int = 900) -> dict:
    """claude -p 로 한 번 실행하고 JSON 결과를 돌려준다 (구독 플랜 과금).

    - ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 을 자식 환경에서 지운다. 파이프라인은 .env 를
      읽어 키를 환경에 올리는데, 이게 CLI 로 넘어가면 조용히 API 과금으로 바뀐다.
    - 프로젝트 CLAUDE.md·훅·MCP 를 읽지 않도록 빈 임시 폴더에서, setting-sources 를 비우고 돈다.
    - 시스템 프롬프트는 파일로 넘긴다 (윈도우 명령줄 길이 한도 회피).
    """
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    with tempfile.TemporaryDirectory() as tmp:
        sys_file = Path(tmp) / "system.md"
        sys_file.write_text(system, encoding="utf-8")
        cmd = [shutil.which("claude") or "claude", "-p", "--model", model, "--output-format", "json",
               "--system-prompt-file", str(sys_file), "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config", "--tools", " ".join(tools) if tools else ""]
        if tools:
            cmd += ["--allowedTools", " ".join(tools)]
        if schema:
            cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        proc = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=tmp,
                              env=env, timeout=timeout)
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise DraftError(f"CLI 출력 파싱 실패 (rc={proc.returncode}): "
                         f"{out[:200]} {proc.stderr.decode('utf-8', 'replace')[:200]}")
    if data.get("is_error") or data.get("subtype") != "success":
        raise DraftError(f"CLI 실패: {data.get('subtype')} {data.get('api_error_status')} "
                         f"{str(data.get('result'))[:200]}")
    return data


def _urls_from(text: str, urls: list[str] | None = None) -> list[str]:
    urls = list(urls or [])
    for u in re.findall(r"https?://[^\s)\]>\"']+", text):
        u = u.rstrip(".,")
        if u not in urls:
            urls.append(u)
    return urls


def _check(resp) -> None:
    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
        raise DraftError(f"모델이 요청을 거절함 (category={cat})")
    if resp.stop_reason == "max_tokens":
        raise DraftError("max_tokens 에 걸려 응답이 잘림")


def research(keyword: str, client: anthropic.Anthropic | None = None, system: str = _RESEARCH_SYSTEM) -> dict:
    """키워드 사실 시트(텍스트)와 검색으로 확인된 출처 URL 목록. 가격형은 system 을 바꿔 부른다."""
    today = date.today().isoformat()
    ask = f"오늘은 {today}. 키워드: {keyword}\n웹 검색은 5회 이내로 하고, 사실 시트를 만들어 줘."
    if _billing() == "subscription":
        data = _run_cli(ask, system, RESEARCH_MODEL, ["WebSearch", "WebFetch"])
        facts_text = data.get("result") or ""
        return {"keyword": keyword, "facts": facts_text, "cited_urls": _urls_from(facts_text),
                "usage": data.get("usage") or {}, "billing": "subscription",
                "notional_cost_usd": data.get("total_cost_usd")}
    client = client or _client()
    with client.messages.stream(
        model=RESEARCH_MODEL, max_tokens=32000,
        system=system,
        output_config={"effort": "medium"},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
                "user_location": {"type": "approximate", "country": "KR"}}],
        messages=[{"role": "user", "content": ask}],
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
    return {"keyword": keyword, "facts": facts_text, "cited_urls": _urls_from(facts_text, urls),
            "usage": resp.usage.to_dict(), "billing": "api"}


def write(keyword: str, blog: str, facts: dict, client: anthropic.Anthropic | None = None,
          feedback: str = "", previous: dict | None = None) -> dict:
    """사실 시트 → 블로그 양식 JSON 초안. feedback·previous 가 있으면 이전 초안을 그 지시대로 고친다."""
    prompt = (f"작성할 블로그: {BLOGS[blog]} (가이드의 해당 섹션 양식을 따른다)\n"
              f"핵심 키워드: {keyword}\n오늘: {date.today().isoformat()}\n\n"
              f"=== 사실 시트 ===\n{facts['facts']}\n\n"
              f"=== 검색으로 확인된 출처 URL ===\n" + "\n".join(facts["cited_urls"]))
    if feedback and previous:
        prev = {k: v for k, v in previous.items() if not k.startswith("_")}
        prompt += ("\n\n=== 이전 초안 (JSON) ===\n" + json.dumps(prev, ensure_ascii=False) +
                   f"\n\n=== 운영자 수정 요청 ===\n{feedback}\n"
                   "이전 초안을 바탕으로 수정 요청을 반영해 전체 초안을 다시 써라. "
                   "사실 시트 밖의 수치는 여전히 쓰지 않는다.")
    if _billing() == "subscription":
        data = _run_cli(prompt, _WRITE_SYSTEM, WRITE_MODEL, [], schema=_DRAFT_SCHEMA)
        draft = data.get("structured_output")
        if not isinstance(draft, dict):
            raise DraftError("구조화 출력이 비어 있음")
        draft["_usage"] = data.get("usage") or {}
        draft["_billing"] = "subscription"
        draft["_notional_cost_usd"] = data.get("total_cost_usd")
        return draft
    client = client or _client()
    with client.messages.stream(
        model=WRITE_MODEL, max_tokens=32000,
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
    draft["_billing"] = "api"
    return draft


def _e(s: str) -> str:
    return html.escape(s, quote=False)


def _rich(s: str, color: str) -> str:
    """**강조** → 굵은 색 글씨. 나머지는 이스케이프."""
    parts = re.split(r"\*\*(.+?)\*\*", s)
    return "".join(f'<b style="color:{color}">{_e(p)}</b>' if i % 2 else _e(p)
                   for i, p in enumerate(parts))


# 블로그별 강조색 (브랜딩 문서와 같은 색)
ACCENT = {"A": "#0f8f7a", "B": "#e8481a", "C": "#3a4fd6", "D": "#6a35e0"}
_KEEP = "word-break:keep-all;overflow-wrap:break-word;"
_CALLOUT = {"tip": ("💡 도움말", "#eef8f4", "#1f8f6a"), "warning": ("⚠️ 주의", "#fff5ec", "#d9730d")}


def _section_head(i: int, text: str, color: str, tistory: bool) -> str:
    if tistory:
        return (f'<h2 id="s{i}" style="margin:36px 0 14px;font-size:22px;{_KEEP}">'
                f'<span style="display:inline-block;background:{color};color:#fff;border-radius:6px;'
                f'padding:0 8px;margin-right:8px;font-size:16px">{i:02d}</span>{_e(text)}</h2>')
    return (f'<hr><p style="font-size:20px;{_KEEP}"><b><span style="color:{color}">{i:02d}</span> '
            f'{_e(text)}</b></p>')


def _table(cols: list[str], rows: list[list[str]], color: str) -> str:
    head = "".join(f'<th style="background:{color};color:#fff;padding:9px 10px;border:1px solid {color};'
                   f'{_KEEP}">{_e(c)}</th>' for c in cols)
    body = "".join("<tr>" + "".join(f'<td style="padding:9px 10px;border:1px solid #ddd;{_KEEP}">'
                                     f'{_rich(c, color)}</td>' for c in row) + "</tr>" for row in rows)
    return (f'<table style="width:100%;border-collapse:collapse;margin:8px 0 18px;font-size:15px">'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def _products_block(products: list[dict], color: str, tistory: bool) -> str:
    """쿠팡 상품 비교표 — 가격·링크는 모델이 아니라 수집 데이터로 코드가 만든다."""
    when = products[0].get("fetched_at", "")
    rows = "".join(
        "<tr>"
        f'<td style="padding:9px 10px;border:1px solid #ddd;text-align:center">{i}</td>'
        f'<td style="padding:9px 10px;border:1px solid #ddd;{_KEEP}">{_e(p["name"])}'
        f'{" 🚀" if p.get("is_rocket") else ""}</td>'
        f'<td style="padding:9px 10px;border:1px solid #ddd;white-space:nowrap"><b>{_e(p["price"])}</b></td>'
        f'<td style="padding:9px 10px;border:1px solid #ddd;text-align:center">'
        f'<a href="{html.escape(p["affiliate_url"])}" style="color:{color};font-weight:700">보러가기</a></td>'
        "</tr>" for i, p in enumerate(products, 1))
    head = "".join(f'<th style="background:{color};color:#fff;padding:9px 10px;border:1px solid {color}">{c}</th>'
                   for c in ("순위", "상품", "현재가", "링크"))
    title = f"💰 쿠팡 인기 상품 가격 ({_e(when)} 기준)"
    heading = (f'<h2 style="margin:36px 0 14px;font-size:22px;{_KEEP}">{title}</h2>' if tistory
               else f'<hr><p style="font-size:20px;{_KEEP}"><b>{title}</b></p>')
    return (heading + f'<table style="width:100%;border-collapse:collapse;margin:8px 0 8px;font-size:15px">'
            f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
            f'<p style="color:#888;font-size:13px;{_KEEP}">순위는 쿠팡 검색 결과 순서이며, 가격은 수시로 바뀝니다. '
            f"구매 전 현재가를 확인하세요. 🚀=로켓배송</p>")


def render_html(draft: dict, images: dict[str, str] | None = None, blog: str = "A",
                products: list[dict] | None = None) -> str:
    """초안 JSON → 본문 HTML.

    네이버(A·B)는 붙여넣기에서 살아남는 요소(굵게·색·표·구분선)만 쓰고, 티스토리(C·D)는
    인라인 CSS 카드·콜아웃·목차까지 쓴다. 어느 쪽이든 한글이 단어 중간에서 줄바꿈되지 않게
    keep-all 을 건다(첫 시험 미리보기에서 "2억"이 "2 / 억"으로 끊겼다).
    images: {capture_url: <img src>} — 단계(steps)가 있는 첫 섹션 아래에 붙인다.
    products: 가격형(B·D) 상품 비교표 — 본문 섹션 뒤, FAQ 앞에 넣는다.
    """
    images = dict(images or {})
    color = ACCENT.get(blog, "#333")
    tistory = blog in ("C", "D")
    p_style = f"margin:0 0 14px;line-height:1.85;font-size:16px;{_KEEP}"
    out = [f'<div style="{_KEEP}">',
           f'<p style="color:#777;font-size:13px;{_KEEP}">🗓 {_e(draft["basis_line"])}</p>',
           f'<p style="{p_style}">{_rich(draft["intro"], color)}</p>']

    summary = "".join(f'<li style="margin:6px 0;{_KEEP}">{_rich(p, color)}</li>' for p in draft["summary_points"])
    if tistory:
        out.append(f'<div style="background:#f6f7fb;border-left:5px solid {color};border-radius:10px;'
                   f'padding:16px 20px;margin:20px 0"><p style="margin:0 0 6px;font-weight:700;color:{color}">'
                   f'📌 핵심 3줄 요약</p><ul style="margin:0;padding-left:20px">{summary}</ul></div>')
        toc = "".join(f'<li style="margin:4px 0"><a href="#s{i}" style="color:#333;text-decoration:none">'
                      f'{_e(sec["heading"])}</a></li>' for i, sec in enumerate(draft["sections"], 1))
        out.append(f'<div style="border:1px solid #e3e3e3;border-radius:10px;padding:14px 20px;margin:0 0 24px">'
                   f'<p style="margin:0 0 6px;font-weight:700">목차</p>'
                   f'<ol style="margin:0;padding-left:20px">{toc}</ol></div>')
    else:
        # 네이버: 한 칸짜리 표는 붙여넣기 뒤에도 테두리 박스로 남는다
        out.append(f'<table style="width:100%;border-collapse:collapse;margin:16px 0"><tr>'
                   f'<td style="background:#f3faf8;border:2px solid {color};padding:14px 18px">'
                   f'<p><b style="color:{color}">📌 핵심 3줄 요약</b></p><ul>{summary}</ul></td></tr></table>')

    for i, sec in enumerate(draft["sections"], 1):
        out.append(_section_head(i, sec["heading"], color, tistory))
        out += [f'<p style="{p_style}">{_rich(p, color)}</p>' for p in sec["paragraphs"]]
        if sec["table_columns"] and sec["table_rows"]:
            out.append(_table(sec["table_columns"], sec["table_rows"], color))
        if sec["steps"]:
            items = "".join(f'<li style="margin:8px 0;{_KEEP}">{_rich(st, color)}</li>' for st in sec["steps"])
            out.append(f'<ol style="padding-left:22px;line-height:1.8">{items}</ol>')
            for url, src in list(images.items()):
                out.append(f'<p style="text-align:center"><img src="{html.escape(src)}" alt="공식 화면 캡처" '
                           f'style="max-width:100%;border:1px solid #ddd;border-radius:8px"></p>'
                           f'<p style="text-align:center;color:#888;font-size:13px">출처: {_e(url)}</p>')
                images.pop(url)
        if sec["checklist"]:
            items = "".join(f'<li style="margin:6px 0;list-style:none;{_KEEP}">✅ {_rich(c, color)}</li>'
                            for c in sec["checklist"])
            out.append(f'<ul style="padding-left:4px">{items}</ul>')
        if sec.get("callout_kind") in _CALLOUT and sec.get("callout_text"):
            label, bg, fg = _CALLOUT[sec["callout_kind"]]
            if tistory:
                out.append(f'<div style="background:{bg};border-radius:10px;padding:12px 16px;margin:10px 0 18px;'
                           f'{_KEEP}"><b style="color:{fg}">{label}</b><br>{_rich(sec["callout_text"], fg)}</div>')
            else:
                out.append(f'<p style="{_KEEP}"><b style="color:{fg}">{label}</b> {_rich(sec["callout_text"], fg)}</p>')

    if products:
        out.append(_products_block(products, color, tistory))
    if draft["faq"]:
        out.append('<h2 style="margin:36px 0 14px;font-size:22px">자주 묻는 질문</h2>' if tistory
                   else '<hr><p style="font-size:20px"><b>자주 묻는 질문</b></p>')
        for f in draft["faq"]:
            out.append(f'<p style="{p_style}"><b style="color:{color}">Q. {_e(f["q"])}</b><br>'
                       f'A. {_rich(f["a"], color)}</p>')
    out.append(f'<p style="{p_style}">{_rich(draft["closing"], color)}</p>')
    if draft["sources"]:
        lis = "".join(f'<li><a href="{html.escape(s["url"])}">{_e(s["title"])}</a></li>' for s in draft["sources"])
        out.append(f'<p style="color:#777;font-size:13px;margin-top:24px"><b>참고 출처</b></p>'
                   f'<ul style="color:#777;font-size:13px">{lis}</ul>')
    out.append("</div>")
    return "\n".join(out)


_PAGE = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
         '<style>body{{max-width:760px;margin:24px auto;padding:0 16px;font-family:"Malgun Gothic",sans-serif;'
         'background:#fff;color:#222;word-break:keep-all}} h1{{font-size:26px;line-height:1.4}}</style>'
         '</head><body><h1>{title}</h1>{body}<p style="color:#888">{tags}</p></body></html>')


def preview_page(draft: dict, blog: str, images: dict[str, str] | None = None,
                 products: list[dict] | None = None) -> str:
    """미리보기 HTML 문서 (제목 + 본문 + 태그) — 발행 전 확인·기록용."""
    return _PAGE.format(title=html.escape(draft["title"]), body=render_html(draft, images, blog, products),
                        tags=" ".join("#" + html.escape(t) for t in draft["tags"]))
