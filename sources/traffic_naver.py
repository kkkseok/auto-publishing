"""네이버 블로그 통계 — 글별 조회수·유입 검색어 (블로그 주인 로그인 필요, 공식 API 없음).

블로그 통계 화면(blog.stat.naver.com)이 부르는 내부 JSON API 를 그 화면 안에서 같은
출처 fetch 로 부른다. 로그인은 블로그별 Aside 계정(NAVER_ASIDE_ACCOUNT_INFO/PRICE)의
세션을 쓴다 — 네이버 자동 로그인은 봇 검사에 막힌다.

  api/blog/rank/cvContentPc  그날 조회수 상위 글 (uri·title·cv)
  api/blog/user/referer/search  그날 유입 검색어 (블로그 단위 — 글 단위가 아니다)

화면 구조나 API 가 바뀌면 NaverStatError 를 낸다 — 수집기는 이를 error 로 남겨
조용히 0건이 되는 일을 막는다. 통계는 하루 늦게 확정된다(오늘 것은 집계 중).
URL 의 블로그 아이디는 별칭일 수 있어(trendscope-498 → bluesky0211) 글은 logNo 로 맞춘다.
"""
from __future__ import annotations

import json
import re

from common.aside_repl import AsideError, AsideRepl

STAT_PAGE = "https://blog.stat.naver.com/blog/daily/daily/cv?blogId={blog_id}"


class NaverStatError(RuntimeError):
    pass


def _rows(payload: dict, data_id: str) -> list[dict]:
    """{columnInfo, rows: {열: [값...]}} → 행 dict 목록. 빈 날은 값이 '' 인 행 하나가 온다."""
    for block in (payload.get("result") or {}).get("statDataList") or []:
        if block.get("dataId") != data_id:
            continue
        rows = (block.get("data") or {}).get("rows") or {}
        cols = list(rows)
        n = len(rows[cols[0]]) if cols else 0
        return [{c: rows[c][i] for c in cols} for i in range(n)]
    raise NaverStatError(f"응답에 {data_id} 없음 — 통계 API 구조가 바뀌었을 수 있음")


def log_no(url: str) -> str:
    m = re.search(r"/(\d{9,})(?:[/?#]|$)", url) or re.search(r"logNo=(\d+)", url)
    return m.group(1) if m else ""


def fetch_days(blog_id: str, days: list[str], account: str = "") -> dict[str, dict]:
    """날짜별 {"posts": [{log_no, title, views}], "queries": [{query, views}]}."""
    out: dict[str, dict] = {}
    try:
        with AsideRepl(account=account) as r:
            r.run("await openTab('about:blank');")
            r.run(f"await page.goto({json.dumps(STAT_PAGE.format(blog_id=blog_id))}, "
                  "{waitUntil: 'domcontentloaded'}); await sleep(4000);", timeout=60)
            for day in days:
                got = {}
                for key, api in (("posts", "blog/rank/cvContentPc"), ("queries", "blog/user/referer/search")):
                    url = f"/api/{api}?timeDimension=DATE&startDate={day}&blogId={blog_id}"
                    res = r.run("var t=await page.evaluate(async(u)=>{const x=await fetch(u,{credentials:'same-origin'}); "
                                "return JSON.stringify({s:x.status, b:await x.text()});}, "
                                f"{json.dumps(url)}); console.log('RESULT '+t)", timeout=60)
                    data = r.result(res)
                    if data.get("s") != 200:
                        raise NaverStatError(f"{api} {day} HTTP {data.get('s')} — 로그인 풀림 또는 API 변경: "
                                             f"{str(data.get('b'))[:150]}")
                    got[key] = json.loads(data["b"])
                posts = [{"log_no": log_no(p.get("uri") or ""), "title": p.get("title") or "", "views": int(p.get("cv") or 0)}
                         for p in _rows(got["posts"], "rankCv") if p.get("uri")]
                queries = [{"query": q["searchQuery"], "views": int(q.get("cv") or 0)}
                           for q in _rows(got["queries"], "refererSearch") if q.get("searchQuery")]
                out[day] = {"posts": posts, "queries": queries}
    except AsideError as e:
        raise NaverStatError(f"Aside({account or '기본'}) 실패: {str(e)[-200:]}")
    return out
