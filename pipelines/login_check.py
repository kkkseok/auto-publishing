"""발행 전 로그인·브라우저 상태 점검 — 풀린 곳이 있으면 텔레그램으로 무엇을 할지 알린다.

발행은 두 브라우저에 걸려 있다 (10-16 결정: 티스토리는 Aside 화면에서 캡차 문제가
그려지지 않아 크롬 확장 유지).

  Aside u0  네이버 A(trendscope-498) 글쓰기 · 서치어드바이저
  Aside u2  네이버 B(taq87641) 글쓰기
  크롬 확장  티스토리 C·D — 브릿지 /healthz 의 확장 마지막 폴링 시각으로 살아 있는지 본다
            (확장은 30초마다 /next 를 묻는다)

Aside 가 꺼져 있으면 common.aside_repl 이 켜고 다시 붙는다. 로그인 풀림은 고칠 수 없으니
알리기만 한다 — 06:30 발행 전에 손볼 시간이 있도록 06:00, 티스토리 발행 전 12:00 에 돈다.

수동 실행: python -m pipelines.login_check
스케줄: SCHEDULE_LOGIN_CHECK
"""
import os
import sys

import requests
from dotenv import load_dotenv
load_dotenv()

from common import blog_drafts
from common.aside_repl import AsideError, AsideRepl
from common.logger import log

SCHEDULE = {
    "env":  "SCHEDULE_LOGIN_CHECK",
    "func": "run",
}

BRIDGE = os.getenv("TISTORY_BRIDGE_URL", "http://127.0.0.1:5757")
EXTENSION_STALE_SEC = 600


def _checks() -> list[tuple[str, str, str, str]]:
    """(이름, Aside 계정, 열 주소, 로그인돼 있으면 주소에 들어 있어야 할 조각)."""
    a_id = os.getenv("NAVER_BLOG_ID_INFO", "")
    b_id = os.getenv("NAVER_BLOG_ID_PRICE", "")
    out = []
    if a_id:
        out.append((f"네이버 A({a_id})", os.getenv("NAVER_ASIDE_ACCOUNT_INFO", ""),
                    f"https://blog.naver.com/{a_id}/postwrite", f"/{a_id}/postwrite"))
    if b_id:
        out.append((f"네이버 B({b_id})", os.getenv("NAVER_ASIDE_ACCOUNT_PRICE", ""),
                    f"https://blog.naver.com/{b_id}/postwrite", f"/{b_id}/postwrite"))
    out.append(("서치어드바이저", os.getenv("NAVER_INDEX_ASIDE_ACCOUNT", ""),
                "https://searchadvisor.naver.com/console/board", "searchadvisor.naver.com/console"))
    return out


def check_aside() -> list[str]:
    problems = []
    by_account: dict[str, list] = {}
    for c in _checks():
        by_account.setdefault(c[1], []).append(c)
    for account, items in by_account.items():
        try:
            with AsideRepl(account=account) as r:
                r.run("await openTab('about:blank');")
                for name, _, url, expect in items:
                    try:
                        out = r.run(f"await page.goto('{url}', {{waitUntil: 'domcontentloaded'}}); await sleep(5000); console.log('URL '+page.url())",
                                    timeout=60)
                    except AsideError as e:   # 한 항목이 막혀도 같은 계정의 나머지는 본다
                        problems.append(f"{name}: 확인 실패 — {str(e)[-120:]}")
                        continue
                    now = out.split("URL ")[-1].split()[0] if "URL " in out else ""
                    if expect in now:
                        log(f"[로그인점검] {name} OK", "ok")
                    else:
                        problems.append(f"{name}: 로그아웃됨 — Aside({account or '기본'}) 창에서 '로그인 상태 유지' 켜고 로그인")
        except AsideError as e:
            problems.append(f"Aside({account or '기본'}) 실행 실패: {str(e)[-150:]}")
    return problems


def check_extension() -> list[str]:
    try:
        h = requests.get(f"{BRIDGE}/healthz", timeout=10).json()
    except (requests.RequestException, ValueError) as e:
        return [f"티스토리 브릿지 응답 없음 (스케줄러 확인): {e}"]
    age = h.get("extension_poll_age_sec")
    if age is None or age > EXTENSION_STALE_SEC:
        return [f"크롬 확장이 {'접속 기록 없음' if age is None else f'{age // 60}분째 응답 없음'} — "
                "크롬이 켜져 있는지, 확장이 켜져 있는지 확인"]
    log(f"[로그인점검] 크롬 확장 OK ({age}초 전 폴링)", "ok")
    return []


def run() -> None:
    problems = check_aside() + check_extension()
    if problems:
        blog_drafts.notify("⚠️ 발행 전 점검에서 문제 발견\n" + "\n".join(f"• {p}" for p in problems))
        for p in problems:
            log(f"[로그인점검] {p}", "error")
        return
    log("[로그인점검] 점검 완료 — 모두 정상", "ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    run()
