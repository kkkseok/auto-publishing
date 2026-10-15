"""네이버 블로그 발행 — Aside 브라우저(실사용자 브라우저)를 REPL 로 조작.

배경 (2026-10-15 검증):
  RabbitWrite 직접 호출(publishers/naver_blog.py)과 쿠키를 옮긴 Playwright 브라우저는
  "권한이 없습니다(no privilege)"로 거부된다. 발행 직전 ncpt 봇 판정 토큰이 붙는데,
  자동화 세션은 통과하지 못한다. Aside 는 사용자가 로그인한 실제 브라우저라 통과한다.

흐름 (한 번의 `aside repl` 실행, 한 줄 스크립트를 stdin 으로 — REPL 은 평가당 120초 제한):
  글쓰기 열기 → 이어쓰기 팝업 취소 → 제목 키 입력 → 본문 HTML 을 편집기 입력 iframe 에
  paste 이벤트로 넣기 → 발행 레이어 → 카테고리 → 태그 → 공개 범위 → 최종 발행 → logNo 확인.

안전장치: 요청한 공개 범위 라디오가 실제로 체크된 것을 확인한 뒤에만 최종 발행을 누른다.
비공개 요청이 공개로 나가는 일을 막기 위해서다.

블로그 B(taq87641)는 다른 네이버 ID 소유라, Aside 의 별도 브라우저 프로필에 그 ID 로
로그인해 두고 NAVER_ASIDE_PROFILE_PRICE 에 프로필 ID 를 넣는다 (`aside host profiles`).
"""
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from common.logger import log

_ASIDE_DEFAULT = Path(os.getenv("LOCALAPPDATA", "")) / "Aside" / "CLI" / "current" / "aside.exe"


@dataclass
class NaverPublishResult:
    success: bool
    url: str = ""
    log_no: str = ""
    message: str = ""


def _aside_bin() -> str:
    return os.getenv("ASIDE_CLI") or shutil.which("aside") or str(_ASIDE_DEFAULT)


def build_script(blog_id: str, title: str, body_html: str, tags: list[str],
                 category: str = "", private: bool = False) -> str:
    """Aside REPL 한 줄 스크립트. 마지막에 `RESULT {json}` 한 줄을 출력한다."""
    plain = re.sub(r"<[^>]+>", "", body_html)
    radio = "open_private" if private else "open_public"
    j = lambda v: json.dumps(v, ensure_ascii=False)
    steps = [
        "const sleep=ms=>new Promise(r=>setTimeout(r,ms)); const out={};",
        f"const page=await openTab('https://blog.naver.com/{blog_id}/postwrite'); await sleep(7000);",
        f"if(!page.url().includes('/{blog_id}/')){{ out.error='wrong blog or not logged in: '+page.url(); }} else {{",
        # 이어쓰기 팝업은 취소 — 지난 자동저장 글을 덮어쓰지 않는다
        "await page.evaluate(()=>{const b=[...document.querySelectorAll('.se-popup button, [class*=popup] button')]"
        ".find(x=>/취소/.test(x.textContent)); if(b) b.click();}); await sleep(800);",
        "await page.click('.se-documentTitle .se-text-paragraph'); await sleep(200);",
        f"await page.keyboard.type({j(title)}, {{delay: 20}}); await sleep(300);",
        "await page.locator('.se-component.se-text .se-text-paragraph').first().click(); await sleep(300);",
        # 편집기 입력은 숨은 iframe 이 받는다 — paste 이벤트를 그 문서에 보낸다
        "out.paste=await page.evaluate(([H,P])=>{const fr=document.activeElement; "
        "const doc=(fr&&fr.contentDocument)?fr.contentDocument:document; const el=doc.activeElement||doc.body; "
        "const dt=new DataTransfer(); dt.setData('text/html',H); dt.setData('text/plain',P); "
        "return !el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}, "
        f"[{j(body_html)}, {j(plain)}]); await sleep(3000);",
        "out.tables=await page.evaluate(()=>document.querySelectorAll('.se-component.se-table').length);",
        "await page.locator('button[class*=publish_btn]').first().click(); await sleep(2000);",
    ]
    if category:
        steps.append(
            "await page.locator('button[class*=selectbox_button]').first().click(); await sleep(800);"
            f"out.category=await page.evaluate((name)=>{{const o=[...document.querySelectorAll('[class*=layer] li, "
            "[role=option]')].find(e=>e.textContent.trim()===name); if(o){(o.querySelector('button,label,span')||o).click(); "
            f"return name;}} return 'not found: '+name;}}, {j(category)}); await sleep(600);")
    for t in tags[:10]:
        steps.append(f"await page.click('#tag-input'); await page.keyboard.type({j(t)}); "
                     "await page.keyboard.press('Enter'); await sleep(150);")
    steps += [
        f"await page.click('label[for={radio}]'); await sleep(500);",
        f"out.visibility=await page.evaluate(()=>document.getElementById('{radio}').checked);",
    ]
    if not private:
        # 공개 글은 검색 허용이 켜져 있어야 노출된다
        steps.append("await page.evaluate(()=>{const c=document.getElementById('publish-option-search'); "
                     "if(c&&!c.checked&&!c.disabled){(document.querySelector('label[for=publish-option-search]')||c).click();}}); "
                     "await sleep(300); out.search=await page.evaluate(()=>{const c=document.getElementById("
                     "'publish-option-search'); return c?c.checked:null;});")
    steps += [
        "if(!out.visibility){ out.error='visibility radio not confirmed — not published'; } else {",
        "await page.locator('button[class*=confirm_btn]').first().click(); await sleep(6000);",
        "out.url=page.url(); const m=out.url.match(/logNo=(\\d+)/); out.logNo=m?m[1]:''; }",
        "}",
        "console.log('RESULT '+JSON.stringify(out));",
    ]
    return " ".join(steps)


def publish(blog_id: str, title: str, body_html: str, tags: list[str], *,
            category: str = "", private: bool = False, profile: str = "",
            timeout: int = 200) -> NaverPublishResult:
    script = build_script(blog_id, title, body_html, tags, category, private)
    cmd = [_aside_bin(), "repl"] + (["--profile", profile] if profile else [])
    try:
        proc = subprocess.run(cmd, input=(script + "\nexit\n").encode("utf-8"),
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return NaverPublishResult(False, message="aside repl 시간 초과")
    except FileNotFoundError:
        return NaverPublishResult(False, message=f"aside CLI 없음: {cmd[0]}")
    text = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout.decode("utf-8", "replace"))
    m = re.search(r"RESULT (\{.*\})", text)
    if not m:
        err = re.findall(r"(?:Error|error)[^\n]{0,200}", text)
        return NaverPublishResult(False, message=(err[-1] if err else text[-300:]).strip())
    out = json.loads(m.group(1))
    if out.get("error"):
        return NaverPublishResult(False, message=out["error"])
    if not out.get("logNo"):
        return NaverPublishResult(False, message=f"발행 후 logNo 없음: {out.get('url')}")
    url = f"https://blog.naver.com/{blog_id}/{out['logNo']}"
    log(f"[네이버/Aside] 발행 {'(비공개) ' if private else ''}{url} · 표 {out.get('tables')} · "
        f"카테고리 {out.get('category', '-')}", "ok")
    return NaverPublishResult(True, url=url, log_no=out["logNo"], message=json.dumps(out, ensure_ascii=False))
