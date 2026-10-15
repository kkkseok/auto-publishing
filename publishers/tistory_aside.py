"""티스토리 발행 — Aside 브라우저(사용자가 로그인한 실제 브라우저)를 REPL 로 조작.

크롬 확장(extension/)이 하던 일을 Aside 가 대신한다: 새 글 열기 → 제목 → 본문(tinymce)
→ 태그 → 발행 레이어 → 공개 범위 → 발행 → (캡차) → 글 주소 확인.

DKAPTCHA 는 사람이 푼다. 캡차가 뜨면 화면을 찍어 브릿지(/captcha/needed)로 텔레그램에
보내고, 사용자가 답글로 보낸 정답을 브릿지(/captcha/answer/<id>)에서 받아 Aside 가 실제
키 입력으로 넣고 제출한다. 텔레그램 답글의 유일한 소비자가 스케줄러 안의 브릿지라서
(getUpdates offset 충돌 방지) 브릿지가 떠 있어야 캡차를 넘길 수 있다.
브릿지는 10분 지난 캡차 대기를 지우므로 답 대기는 그보다 짧게(기본 540초) 잡는다.

안전장치: 요청한 공개 범위 라디오가 실제로 체크된 것을 확인한 뒤에만 발행을 누르고,
발행 직전 에디터의 제목·본문 길이를 다시 확인한다.
"""
import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from common.logger import log
from publishers.aside_repl import AsideError, AsideRepl

BRIDGE = os.getenv("TISTORY_BRIDGE_URL", "http://127.0.0.1:5757")
BRIDGE_HEADERS = {"X-Bridge-Client": "auto-publishing-ext"}
CAPTCHA_WAIT_SEC = int(os.getenv("TISTORY_CAPTCHA_WAIT_SEC", "540"))
CAPTCHA_ROUNDS = 3

# 캡차 위젯 감지 — 확장 content.js 와 같은 후보
_CAPTCHA_PROBE = ("()=>!!(document.querySelector('iframe[src*=dkaptcha]')||document.querySelector('[class*=dkaptcha]')"
                  "||document.querySelector('iframe[src*=captcha]'))")


@dataclass
class TistoryPublishResult:
    success: bool
    url: str = ""
    post_id: str = ""
    message: str = ""


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _fill_and_publish(r: AsideRepl, blog: str, title: str, html: str, tags: list[str], visibility: int) -> dict:
    r.run(f"await openTab('about:blank'); page.on('dialog', d=>d.dismiss()); "
          f"await page.goto('https://{blog}.tistory.com/manage/newpost'); "
          "for(let i=0;i<40;i++){ if(await page.evaluate(()=>!!(window.tinymce&&window.tinymce.activeEditor))) break; await sleep(500);} "
          "console.log('RESULT '+JSON.stringify({url:page.url()}))")
    # 이어쓰기 커스텀 레이어는 '취소'로 새 글 시작 (native confirm 은 dialog 핸들러가 거절)
    r.run(f"await page.evaluate(()=>{{const l=[...document.querySelectorAll('[class*=layer],[class*=modal],[role=dialog]')]"
          ".filter(e=>e.offsetParent&&/이어서\\s*작성|저장된\\s*글|임시\\s*저장/.test(e.innerText||''));"
          "for(const x of l){const b=[...x.querySelectorAll('button')].find(b=>/취소|새\\s*글/.test(b.innerText||'')); if(b) b.click();}}); "
          "await sleep(500); console.log('RESULT {}')")
    pre = AsideRepl.result(r.run(
        f"var out={{url:page.url()}}; "
        "out.ready=await page.evaluate(()=>!!(window.tinymce&&window.tinymce.activeEditor)); "
        "if(out.ready){ "
        f"await page.fill('#post-title-inp', {_j(title)}); "
        "out.len=await page.evaluate((h)=>{const e=tinymce.activeEditor; e.setContent(h); "
        "const ta=document.getElementById(e.id); if(ta){ta.value=e.getContent(); ta.dispatchEvent(new Event('input',{bubbles:true}));} "
        f"e.save(); e.setDirty(true); e.fire('change'); e.fire('input'); return e.getContent().length;}}, {_j(html)}); "
        "} console.log('RESULT '+JSON.stringify(out))"))
    if not pre.get("ready"):
        return {"error": f"에디터 미로딩 (로그인 필요?) {pre.get('url')}"}
    for t in tags[:10]:
        r.run(f"await page.click('#tagText'); await page.keyboard.type({_j(t)}, {{delay: 20}}); "
              "await page.keyboard.press('Enter'); await sleep(250);")
    return AsideRepl.result(r.run(
        f"var out={{}}; "
        "out.title=await page.evaluate(()=>document.querySelector('#post-title-inp').value); "
        "out.len=await page.evaluate(()=>tinymce.activeEditor.getContent().length); "
        f"if(out.title.trim()!=={_j(title.strip())}){{ out.error='에디터 제목 불일치: '+out.title.slice(0,40); }} "
        f"else if(out.len<{len(html)}*0.5){{ out.error='에디터 본문 길이 이상: '+out.len; }} else {{ "
        "await page.click('#publish-layer-btn'); await sleep(1500); "
        f"out.checked=await page.evaluate((v)=>{{const el=document.querySelector('.inner_editor_layer input[value=\"'+v+'\"]')"
        f"||document.querySelector('input[value=\"'+v+'\"]'); if(!el) return false; el.click(); return el.checked;}}, '{visibility}'); "
        "await sleep(400); "
        "if(!out.checked){ out.error='공개 범위 라디오 확인 실패 — 발행 중단'; } else { "
        "await page.click('#publish-btn'); out.clicked=true; "
        f"for(let i=0;i<60;i++){{ await sleep(500); if(await page.evaluate({_CAPTCHA_PROBE})){{ out.captcha=true; break; }} "
        "if(!page.url().includes('/newpost')) break; } } } "
        "out.url=page.url(); console.log('RESULT '+JSON.stringify(out))"))


def _shot_captcha(r: AsideRepl, png: Path) -> None:
    """캡차 iframe 부분만 잘라 찍는다 (휴대폰에서 읽기 쉽게). 지도 이미지 로딩을 잠깐 기다린다."""
    r.run("await sleep(2500); var rc=await page.evaluate(()=>{const f=document.querySelector('iframe[src*=dkaptcha]')"
          "||document.querySelector('iframe[src*=captcha]'); if(!f) return null; const b=f.getBoundingClientRect(); "
          "return {x:Math.max(0,b.x),y:Math.max(0,b.y),width:b.width,height:b.height};}); "
          f"await page.screenshot(rc?{{path: {_j(str(png))}, clip: rc}}:{{path: {_j(str(png))}}});")


def _relay_captcha(item_id: str, png: Path, title: str) -> str:
    """캡차 화면을 텔레그램으로 보내고 사용자의 답글을 기다린다. 못 받으면 ''."""
    b64 = base64.b64encode(png.read_bytes()).decode()
    try:
        resp = requests.post(f"{BRIDGE}/captcha/needed", headers=BRIDGE_HEADERS, timeout=20,
                             json={"id": item_id, "image_b64": b64, "title": title})
    except requests.RequestException as e:
        raise AsideError(f"브릿지 연결 실패 (스케줄러가 꺼져 있나?): {e}")
    if not resp.ok:
        raise AsideError(f"캡차 텔레그램 전송 실패: {resp.status_code} {resp.text[:200]}")
    log(f"[티스토리/Aside] 캡차 텔레그램 전송 — 답글 대기 (최대 {CAPTCHA_WAIT_SEC}s)", "info")
    deadline = time.time() + CAPTCHA_WAIT_SEC
    while time.time() < deadline:
        a = requests.get(f"{BRIDGE}/captcha/answer/{item_id}", headers=BRIDGE_HEADERS, timeout=10)
        if a.status_code == 200:
            return a.json().get("answer", "")
        time.sleep(3)
    return ""


def _submit_captcha(r: AsideRepl, answer: str) -> dict:
    """사람이 보낸 정답을 입력하고 제출한다.

    캡차는 다른 도메인 iframe 이라 page.frames()/frameLocator 로는 안 잡힌다. REPL 내장
    snapshot() 은 iframe 안 요소까지 ref 로 보여 주므로 그 ref 로 클릭한다. 입력은 실제
    키 입력이라 확장처럼 dirty/touched 플래그를 흉내 낼 필요가 없다.
    """
    return AsideRepl.result(r.run(
        "var out={}; var snp=await snapshot(page,{interactive:true}); "
        "var mi=snp.tree.match(/textbox \"정답을 입력해주세요\" \\[ref=(\\w+)\\]/); "
        "if(!mi){ out.error='캡차 입력칸 없음'; } else { "
        f"await page.locator(mi[1]).click(); await page.keyboard.type({_j(answer)}, {{delay: 90}}); await sleep(600); "
        "snp=await snapshot(page,{interactive:true}); var mb=snp.tree.match(/button \"답변 제출\" \\[ref=(\\w+)\\]/); "
        "if(mb){ await page.locator(mb[1]).click(); out.submitted='button'; } else { await page.keyboard.press('Enter'); out.submitted='enter'; } "
        "for(let i=0;i<30;i++){ await sleep(1000); if(!page.url().includes('/newpost')) break; "
        f"if(i>4 && await page.evaluate({_CAPTCHA_PROBE})) {{ out.again=true; break; }} }} }} "
        "out.url=page.url(); console.log('RESULT '+JSON.stringify(out))"))


def _find_post(r: AsideRepl, blog: str, title: str) -> dict:
    return AsideRepl.result(r.run(
        f"var found=await page.evaluate(async([b,t])=>{{const norm=s=>String(s||'').replace(/[^0-9a-zA-Z가-힣]/g,''); "
        "const res=await fetch('https://'+b+'.tistory.com/manage/posts.json?page=1',{credentials:'include'}); const d=await res.json(); "
        "const items=d.items||d.posts||(d.data&&(d.data.items||d.data.posts))||[]; "
        "const it=items.find(i=>norm(i.title)===norm(t)); return it?{id:String(it.id),permalink:it.permalink||''}:null;}, "
        f"[{_j(blog)}, {_j(title)}]); console.log('RESULT '+JSON.stringify({{found}}))"))


def publish(blog: str, title: str, body_html: str, tags: list[str], *, item_id: str,
            private: bool = False) -> TistoryPublishResult:
    visibility = 0 if private else 20
    try:
        with AsideRepl() as r:
            out = _fill_and_publish(r, blog, title, body_html, tags, visibility)
            if out.get("error"):
                return TistoryPublishResult(False, message=out["error"])
            for n in range(CAPTCHA_ROUNDS):
                if not out.get("captcha") and not out.get("again"):
                    break
                png = Path(r.session_dir) / f"captcha{n}.png"
                _shot_captcha(r, png)
                answer = _relay_captcha(item_id, png, title)
                if not answer:
                    return TistoryPublishResult(False, message=f"캡차 답 미수신 ({CAPTCHA_WAIT_SEC}s)")
                out = _submit_captcha(r, answer)
                if out.get("error"):
                    return TistoryPublishResult(False, message=out["error"])
                log(f"[티스토리/Aside] 캡차 {n + 1}회차 제출 → {out.get('url')}", "info")
            if "/newpost" in out.get("url", ""):
                return TistoryPublishResult(False, message=f"발행 후 페이지 이동 없음: {out}")
            found = _find_post(r, blog, title).get("found")
    except AsideError as e:
        return TistoryPublishResult(False, message=str(e)[-400:])
    if not found:
        return TistoryPublishResult(False, message="발행 후 글 목록에서 제목을 찾지 못함 — 수동 확인 필요")
    url = found["permalink"] or f"https://{blog}.tistory.com/{found['id']}"
    log(f"[티스토리/Aside] 발행 {'(비공개) ' if private else ''}{url}", "ok")
    return TistoryPublishResult(True, url=url, post_id=found["id"])
