"""글 대표 썸네일 — 제목·핵심 3줄을 블로그 색 카드(1080×1080 PNG)로 렌더링.

AI 이미지 API 가 무료 등급에서 막혀 있어 코드로 그린다(브랜딩 이미지와 같은 방식,
tools/render_blog_brand.py). 정사각형은 네이버 목록·검색 썸네일과 티스토리 대표이미지
양쪽에서 잘리지 않는다. 글자는 데이터(초안 JSON)에서만 가져온다.

    png = render(draft, "A", out_dir)
"""
import html
import re
from datetime import date
from pathlib import Path

# tools/render_blog_brand.py 의 BLOGS 와 같은 이름·색
BRAND = {
    "A": dict(name="생활력 만렙", g1="#12c2a2", g2="#0a7f8c", c2="#ffb020"),
    "B": dict(name="살까말까 데이터룸", g1="#ff8a3d", g2="#e8481a", c2="#2b59ff"),
    "C": dict(name="오늘의 생활 치트키", g1="#6d82ff", g2="#3a3fc9", c2="#ffcc3d"),
    "D": dict(name="프라이스 레이더", g1="#8b5cff", g2="#3b1a9e", c2="#38f2c3"),
}

_CSS = """
*{margin:0;box-sizing:border-box}
body{width:1080px;height:1080px;font-family:'Malgun Gothic','Noto Sans KR',sans-serif;word-break:keep-all}
.bg{position:relative;width:1080px;height:1080px;overflow:hidden;padding:88px 84px;
    display:flex;flex-direction:column;background:linear-gradient(150deg,var(--g1),var(--g2));color:#fff}
.blob{position:absolute;border-radius:50%;background:#fff;opacity:.12}
.kw{display:inline-block;align-self:flex-start;background:var(--c2);color:#1b1b1b;font-weight:800;
    font-size:34px;padding:10px 26px;border-radius:40px;margin-bottom:34px;position:relative}
h1{font-size:var(--fs);line-height:1.28;font-weight:800;letter-spacing:-1.5px;position:relative;
   text-shadow:0 4px 18px rgba(0,0,0,.18)}
.card{margin-top:auto;background:rgba(255,255,255,.96);color:#222;border-radius:34px;padding:34px 40px;
      position:relative;box-shadow:0 24px 50px rgba(0,0,0,.2)}
.card li{list-style:none;font-size:31px;line-height:1.45;font-weight:600;padding-left:46px;position:relative;margin:14px 0}
.card li:before{content:'✓';position:absolute;left:0;top:0;color:var(--g2);font-weight:900}
.foot{display:flex;justify-content:space-between;align-items:center;margin-top:30px;font-size:28px;
      font-weight:700;opacity:.95;position:relative}
"""


def _plain(s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s).strip()


def _short(s: str, n: int) -> str:
    s = _plain(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(",·:.") + "…"


def card_html(draft: dict, blog: str, keyword: str = "") -> str:
    b = BRAND[blog]
    title = _plain(draft["title"])
    # 제목 길이에 맞춰 글자 크기 — 네 줄 안에 들어가게
    fs = 78 if len(title) <= 28 else 68 if len(title) <= 40 else 60
    points = "".join(f"<li>{html.escape(_short(p, 52))}</li>" for p in draft.get("summary_points", [])[:3])
    kw = f'<div class="kw">{html.escape(keyword)}</div>' if keyword else ""
    return (f"<html><head><meta charset='utf-8'><style>{_CSS}"
            f":root{{--g1:{b['g1']};--g2:{b['g2']};--c2:{b['c2']};--fs:{fs}px}}</style></head><body>"
            f'<div class="bg"><div class="blob" style="width:560px;height:560px;right:-200px;top:-220px"></div>'
            f'<div class="blob" style="width:380px;height:380px;left:-160px;bottom:220px"></div>'
            f"{kw}<h1>{html.escape(title)}</h1>"
            f'<ul class="card">{points}</ul>'
            f'<div class="foot"><span>{html.escape(b["name"])}</span><span>{date.today():%Y.%m.%d} 기준</span></div>'
            "</div></body></html>")


def render(draft: dict, blog: str, out_dir: Path, keyword: str = "") -> Path:
    from playwright.sync_api import sync_playwright
    out = Path(out_dir) / f"{blog}_thumb.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1080})
        page.set_content(card_html(draft, blog, keyword))
        page.wait_for_timeout(300)
        page.screenshot(path=str(out))
        browser.close()
    return out
