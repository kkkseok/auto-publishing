"""공식 사이트 화면 캡처 — 생활정보 글의 '신청 방법' 단계 이미지.

로그인 없이 보이는 첫 화면만 찍는다(1280×900, 모바일 가독성을 위해 장식 여백 없이).
실패한 URL 은 건너뛰고, 성공한 것만 {url: 파일경로} 로 돌려준다.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from common.logger import log


def capture(urls: list[str], out_dir: Path, limit: int = 4) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR")
        for i, url in enumerate(urls[:limit], 1):
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(2500)
                path = out_dir / f"capture_{i}.png"
                page.screenshot(path=str(path))
                saved[url] = str(path)
            except Exception as e:
                log(f"[캡처] 실패 {url}: {e}", "warn")
            finally:
                page.close()
        browser.close()
    return saved
