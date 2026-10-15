"""4개 블로그 프로필(1024²)·모바일 커버(1080×1350)를 HTML/SVG 로 디자인해 PNG 로 렌더링.

AI 이미지 API 가 무료 등급에서 막혀 있어 코드로 그린다. 문구·색은 BLOGS 를 고친 뒤 실행:
    .venv/Scripts/python.exe tools/render_blog_brand.py <출력 폴더>
"""
import os
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

# 아이콘은 단순한 SVG 도형 — 64px 로 줄여도 알아볼 수 있게 선을 굵게
ICONS = {
    "check": '<rect x="18" y="12" width="64" height="78" rx="10" fill="#fff"/>'
             '<rect x="36" y="6" width="28" height="14" rx="5" fill="#fff" opacity=".85"/>'
             '<path d="M30 40l8 8 14-16M30 62l8 8 14-16" stroke="var(--c1)" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
             '<path d="M58 44h12M58 66h12" stroke="var(--c2)" stroke-width="6" stroke-linecap="round"/>',
    "chart": '<rect x="10" y="12" width="80" height="76" rx="14" fill="#fff"/>'
             '<path d="M20 34l16 12 14-6 14 24 16-16" stroke="var(--c1)" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
             '<circle cx="64" cy="64" r="8" fill="var(--c2)"/>'
             '<path d="M20 78h60" stroke="var(--c2)" stroke-width="4" stroke-linecap="round" opacity=".5"/>',
    "key": '<circle cx="34" cy="50" r="22" fill="#fff"/><circle cx="34" cy="50" r="9" fill="var(--c1)"/>'
           '<path d="M54 50h34M74 50v14M86 50v10" stroke="#fff" stroke-width="10" stroke-linecap="round"/>'
           '<circle cx="80" cy="22" r="7" fill="var(--c2)"/>',
    "radar": '<circle cx="50" cy="50" r="40" fill="none" stroke="#fff" stroke-width="6"/>'
             '<circle cx="50" cy="50" r="24" fill="none" stroke="#fff" stroke-width="4" opacity=".7"/>'
             '<path d="M50 50L82 26" stroke="#fff" stroke-width="6" stroke-linecap="round"/>'
             '<circle cx="50" cy="50" r="6" fill="#fff"/><circle cx="70" cy="66" r="8" fill="var(--c2)"/>',
}

BLOGS = {
    "A": dict(name="생활력 만렙", tag="놓치면 손해인 생활 정보, 표 한 장으로", icon="check",
              c1="#0fa38b", c2="#ffb020", g1="#12c2a2", g2="#0a7f8c", chips=["지원금", "신청방법", "생활요금"]),
    "B": dict(name="살까말까 데이터룸", tag="쿠팡 인기템 가격, 매일 기록해요", icon="chart",
              c1="#f0661f", c2="#2b59ff", g1="#ff8a3d", g2="#e8481a", chips=["가격 그래프", "역대 최저가", "구매 타이밍"]),
    "C": dict(name="오늘의 생활 치트키", tag="알아두면 바로 쓰는 생활 정보", icon="key",
              c1="#4b63e8", c2="#ffcc3d", g1="#6d82ff", g2="#3a3fc9", chips=["3분 정리", "공식 출처", "체크리스트"]),
    "D": dict(name="프라이스 레이더", tag="가격 그래프로 잡는 쇼핑 타이밍", icon="radar",
              c1="#7a3ff0", c2="#38f2c3", g1="#8b5cff", g2="#3b1a9e", chips=["최저가 알림", "가격 변동", "구매 가이드"]),
}

BASE_CSS = """
*{margin:0;box-sizing:border-box}
body{font-family:'Malgun Gothic','Noto Sans KR',sans-serif;}
.bg{position:relative;overflow:hidden;background:linear-gradient(145deg,var(--g1),var(--g2));}
.blob{position:absolute;border-radius:50%;filter:blur(2px);opacity:.28;background:#fff}
.glass{background:rgba(255,255,255,.18);border:2px solid rgba(255,255,255,.45);
       box-shadow:0 30px 60px rgba(0,0,0,.18);backdrop-filter:blur(6px)}
"""


def profile_html(b):
    return f"""<html><head><style>{BASE_CSS}
:root{{--c1:{b['c1']};--c2:{b['c2']};--g1:{b['g1']};--g2:{b['g2']}}}
.bg{{width:1024px;height:1024px;display:flex;align-items:center;justify-content:center}}
.tile{{width:640px;height:640px;border-radius:170px;display:flex;align-items:center;justify-content:center}}
</style></head><body><div class="bg">
<div class="blob" style="width:520px;height:520px;left:-160px;top:-180px"></div>
<div class="blob" style="width:420px;height:420px;right:-140px;bottom:-160px;opacity:.18"></div>
<div class="tile glass"><svg width="420" height="420" viewBox="0 0 100 100">{ICONS[b['icon']]}</svg></div>
</div></body></html>"""


def cover_html(b):
    chips = "".join(f'<span class="chip">{c}</span>' for c in b["chips"])
    return f"""<html><head><style>{BASE_CSS}
:root{{--c1:{b['c1']};--c2:{b['c2']};--g1:{b['g1']};--g2:{b['g2']}}}
.bg{{width:1080px;height:1350px}}
.icon{{position:absolute;left:50%;top:330px;transform:translate(-50%,-50%);width:440px;height:440px;
      border-radius:120px;display:flex;align-items:center;justify-content:center}}
.card{{position:absolute;left:60px;right:60px;bottom:70px;height:420px;border-radius:48px;background:#fff;
      padding:64px 64px;box-shadow:0 30px 70px rgba(0,0,0,.22)}}
.bar{{width:64px;height:10px;border-radius:5px;background:var(--c1);margin-bottom:34px}}
h1{{font-size:96px;font-weight:800;letter-spacing:-3px;color:#16171c;line-height:1.05}}
p{{margin-top:22px;font-size:42px;font-weight:600;color:var(--c1);letter-spacing:-1px}}
.chips{{position:absolute;left:0;right:0;top:640px;display:flex;justify-content:center;gap:18px}}
.chip{{font-size:34px;font-weight:700;color:#fff;padding:14px 30px;border-radius:999px;
      background:rgba(255,255,255,.2);border:2px solid rgba(255,255,255,.5)}}
</style></head><body><div class="bg">
<div class="blob" style="width:700px;height:700px;left:-260px;top:-260px"></div>
<div class="blob" style="width:520px;height:520px;right:-200px;top:380px;opacity:.16"></div>
<div class="icon glass"><svg width="300" height="300" viewBox="0 0 100 100">{ICONS[b['icon']]}</svg></div>
<div class="chips">{chips}</div>
<div class="card"><div class="bar"></div><h1>{b['name']}</h1><p>{b['tag']}</p></div>
</div></body></html>"""


with sync_playwright() as p:
    br = p.chromium.launch()
    for k, b in BLOGS.items():
        for kind, html, (w, h) in (("profile", profile_html(b), (1024, 1024)),
                                   ("cover", cover_html(b), (1080, 1350))):
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.set_content(html)
            pg.wait_for_timeout(200)
            pg.screenshot(path=os.path.join(OUT, f"261015_{k}-{kind}.png"))
            pg.close()
        print("rendered", k)
    br.close()
