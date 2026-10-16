"""
Naver Search Advisor 색인 제출 — Aside 브라우저(사용자가 로그인한 실제 브라우저)로.

예전에는 Playwright persistent context 에 아이디·비밀번호로 자동 로그인했는데, 10-14 부터
"로그인 페이지에 머무름"으로 매일 전량 실패했다 (네이버 블로그 RabbitWrite 차단과 같은
자동화 세션 차단으로 보임). 서치어드바이저에 로그인된 Aside 계정에서 사이트별 '웹 페이지
수집 요청' 화면을 열고, 그 페이지의 csrfToken·enc_id 로 같은 출처 fetch 를 보낸다.

하루 50개 한도. FAIL_MAX_DOCUMENT_COUNT 응답이면 즉시 중단.

환경변수:
    NAVER_INDEX_ASIDE_ACCOUNT   서치어드바이저에 로그인된 Aside 계정 (기본: 현재 계정, u0)
"""
import json
import os
from urllib.parse import quote, urlparse

from common.aside_repl import AsideError, AsideRepl
from common.logger import log

CRAWL_ENDPOINT = "https://searchadvisor.naver.com/api-console/request/crawl"
DAILY_LIMIT = 50
REQUEST_INTERVAL_MS = 10000  # 구 코드 INDEXING_WAIT_TIME


def _site_base(url: str) -> str:
    """URL에서 사이트 기본 주소(scheme+host) 추출."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _relative_path(url: str, site: str) -> str:
    """URL에서 사이트 주소를 뺀 상대 경로."""
    return url.replace(site + "/", "").replace(site, "")


def _submit_js(site: str, document: str) -> str:
    """수집 요청 화면을 열어 토큰을 읽고 제출 — `RESULT {message, login}` 한 줄을 찍는다."""
    page_url = f"https://searchadvisor.naver.com/console/site/request/crawl?site={quote(site, safe='')}"
    j = lambda v: json.dumps(v, ensure_ascii=False)
    return (
        f"await page.goto({j(page_url)}); await sleep(2500); var out={{}}; "
        "var src=await page.evaluate(()=>{for(const s of document.querySelectorAll('script')) "
        "if(s.textContent.includes('csrfToken')) return s.textContent; return '';}); "
        "var csrf=(src.split('csrfToken:\"')[1]||'').split('\",')[0]; "
        "var enc=(src.split('enc_id:\"')[1]||'').split('\",')[0]; "
        "if(!csrf||!enc){ out.login=!/nid\\.naver\\.com/.test(page.url()); out.message='NO_TOKEN'; } else { "
        f"out.message=await page.evaluate(async([ep,body,ref])=>{{const r=await fetch(ep,{{method:'POST',credentials:'include',"
        "headers:{'accept':'application/json, text/plain, */*','content-type':'application/json;charset=UTF-8'},"
        "body:JSON.stringify(body)}); try{ return (await r.json()).message||('HTTP '+r.status);}catch(e){ return 'HTTP '+r.status; }}, "
        f"[{j(CRAWL_ENDPOINT)}, {{user_enc_id: enc, site: {j(site)}, document: {j(document)}, _csrf: csrf}}, {j(page_url)}]); }} "
        "console.log('RESULT '+JSON.stringify(out))"
    )


def submit_urls(urls: list) -> dict:
    """URL 목록을 제출하고 {url: "ok" | "limit" | "error"} 를 돌려준다."""
    results: dict = {}
    if not urls:
        return results
    account = os.getenv("NAVER_INDEX_ASIDE_ACCOUNT", "")
    try:
        with AsideRepl(account=account) as r:
            r.run("await openTab('about:blank');")
            for idx, url in enumerate(urls[:DAILY_LIMIT], start=1):
                site = _site_base(url)
                try:
                    out = AsideRepl.result(r.run(_submit_js(site, _relative_path(url, site)), timeout=90))
                except AsideError as e:
                    log(f"[Naver 색인] {idx}. 예외: {url} — {str(e)[-200:]}", "error")
                    results[url] = "error"
                    continue
                message = out.get("message", "")
                if message in ("SUCCESS", "FAIL_ALREADY_CRAWLED"):   # 이미 수집 요청된 문서도 목적은 달성
                    log(f"[Naver 색인] {idx}. OK: {url}", "ok")
                    results[url] = "ok"
                elif message == "FAIL_MAX_DOCUMENT_COUNT":
                    log("[Naver 색인] 일일 한도(50개) 초과 — 중단", "warn")
                    for u in urls[idx - 1:]:
                        results[u] = "limit"
                    break
                elif message == "NO_TOKEN" and idx == 1:
                    # 첫 URL 부터 토큰이 없으면 개별 URL 이 아니라 로그인 문제 — 전량 실패로 드러낸다
                    log("[Naver 색인] 서치어드바이저 토큰 없음 — Aside 계정"
                        f"({account or '기본'})의 서치어드바이저 로그인 확인 필요 (login={out.get('login')})", "error")
                    for u in urls[:DAILY_LIMIT]:
                        results.setdefault(u, "error")
                    break
                else:
                    log(f"[Naver 색인] {idx}. 실패 [{message}]: {url}", "warn")
                    results[url] = "error"
                if idx < min(len(urls), DAILY_LIMIT):
                    r.run(f"await sleep({REQUEST_INTERVAL_MS});", timeout=30)
    except AsideError as e:
        log(f"[Naver 색인] Aside 실행 실패: {str(e)[-300:]}", "error")
        for u in urls[:DAILY_LIMIT]:
            results.setdefault(u, "error")
    ok_count = sum(1 for s in results.values() if s == "ok")
    log(f"[Naver 색인] 완료: {ok_count}/{len(urls)}건 성공", "step")
    return results
