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
로그인해 둔다. Aside 는 새 프로필을 별도 로컬 계정(u1, u2 …)으로 붙이므로 `--account` 로
고른다: NAVER_ASIDE_ACCOUNT_PRICE='u2' (`aside account list` 로 확인).
"""
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from common.aside_repl import NOT_RUNNING, start_aside
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


_NOTICE_MARK = "<!--affiliate-notice-->"


def _split_notice(body_html: str) -> tuple[str, str]:
    """대가성 고지 박스(맨 앞)와 나머지 — 썸네일은 고지 다음에 들어가야 한다."""
    if not body_html.startswith(_NOTICE_MARK):
        return "", body_html
    end = body_html.index("</div>") + len("</div>")
    return body_html[:end], body_html[end:]


def build_script(blog_id: str, title: str, body_html: str, tags: list[str],
                 category: str = "", private: bool = False, image_b64: str = "") -> str:
    """Aside REPL 한 줄 스크립트. 마지막에 `RESULT {json}` 한 줄을 출력한다.

    image_b64(PNG)가 있으면 편집기에 파일로 붙여넣는다 — SmartEditor 가 네이버 서버
    (blogfiles.pstatic.net)에 올리고 대표 이미지가 된다. 순서: 고지 → 썸네일 → 본문.
    """
    notice, rest = _split_notice(body_html) if image_b64 else ("", body_html)
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
        # 편집기 입력은 숨은 iframe 이 받는다 — paste 이벤트를 그 문서에 보낸다.
        # H 가 있으면 HTML, F(base64 PNG)가 있으면 파일을 붙여넣는다.
        "const paste=(H,F)=>page.evaluate(([H,F])=>{const fr=document.activeElement; "
        "const doc=(fr&&fr.contentDocument)?fr.contentDocument:document; const el=doc.activeElement||doc.body; "
        "const dt=new DataTransfer(); if(F){const b=atob(F); const u=new Uint8Array(b.length); "
        "for(let i=0;i<b.length;i++) u[i]=b.charCodeAt(i); dt.items.add(new File([u],'thumbnail.png',{type:'image/png'}));} "
        "else {dt.setData('text/html',H); dt.setData('text/plain',H.replace(/<[^>]+>/g,''));} "
        "return !el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));}, [H,F]);",
        "const lastText=async()=>{await page.locator('.se-component.se-text .se-text-paragraph').last().click(); await sleep(300);};",
    ]
    if notice:
        steps.append(f"await paste({j(notice)}, ''); await sleep(1500); await lastText();")
    if image_b64:
        steps.append(f"out.image=await paste('', {j(image_b64)}); await sleep(8000); "
                     "out.images=await page.evaluate(()=>[...document.querySelectorAll('.se-component.se-image img')]"
                     ".filter(i=>i.src.includes('pstatic.net')).length); await lastText();")
    steps += [
        f"out.paste=await paste({j(rest)}, ''); await sleep(3000);",
        "out.tables=await page.evaluate(()=>document.querySelectorAll('.se-component.se-table').length);",
        "await page.locator('button[class*=publish_btn]').first().click(); await sleep(2000);",
    ]
    if category:
        steps.append(
            "await page.locator('button[class*=selectbox_button]').first().click(); await sleep(800);"
            # 목록은 라디오 + label[for="<번호>_<이름>"] — span 이 아니라 label 을 실제 클릭해야 선택된다
            f"out.category=await page.evaluate((name)=>{{const l=[...document.querySelectorAll('[class*=option_list_layer] label')]"
            ".find(e=>e.textContent.trim()===name); return l?l.getAttribute('for'):null;}, "
            f"{j(category)}); if(out.category){{ await page.click('label[for=\"'+out.category+'\"]'); }} "
            f"else {{ out.category='not found: '+{j(category)}; }} await sleep(600);"
            # 선택이 실제로 반영됐는지 — 셀렉트 버튼에 보이는 이름을 읽는다
            "out.categoryShown=await page.evaluate(()=>{const b=document.querySelector('button[class*=selectbox_button]'); "
            "return b?b.innerText.trim():null;});")
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
            category: str = "", private: bool = False, profile: str = "", account: str = "",
            image_b64: str = "", timeout: int = 240) -> NaverPublishResult:
    script = build_script(blog_id, title, body_html, tags, category, private, image_b64)
    cmd = ([_aside_bin(), "repl"] + (["--account", account] if account else [])
           + (["--profile", profile] if profile else []))
    for attempt in range(2):
        try:
            proc = subprocess.run(cmd, input=(script + "\nexit\n").encode("utf-8"),
                                  capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return NaverPublishResult(False, message="aside repl 시간 초과")
        except FileNotFoundError:
            return NaverPublishResult(False, message=f"aside CLI 없음: {cmd[0]}")
        # 사용자가 Aside 창을 닫아 두었으면 켜고 한 번 더 — 실패면 아래에서 그대로 드러난다
        if attempt == 0 and NOT_RUNNING.encode() in proc.stdout + proc.stderr and start_aside():
            log("[네이버/Aside] Aside 가 꺼져 있어 실행 후 재시도", "warn")
            continue
        break
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
        f"카테고리 {out.get('categoryShown') or out.get('category', '-')}", "ok")
    return NaverPublishResult(True, url=url, log_no=out["logNo"], message=json.dumps(out, ensure_ascii=False))
