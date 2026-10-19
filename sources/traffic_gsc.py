"""구글 서치콘솔 검색 실적 — 글·검색어별 노출·클릭·순위 (공식 API, 읽기 전용).

인증은 ADC(gcloud auth application-default login) — webmasters.readonly 범위가 있어야 한다.
2026-10-19 범위 추가·Search Console API 사용 설정 완료 (프로젝트 auto-publishing-495911).

서치콘솔은 2~3일 늦게 확정되므로 수집기는 최근 며칠을 매번 다시 받는다.
검색어 차원(query)을 넣으면 개인정보 보호로 걸러진 검색어가 빠져 합계가 작아진다 —
그래서 글 단위 합계(page)와 검색어 단위(page+query)를 따로 받는다.
"""
from __future__ import annotations

from urllib.parse import quote

import google.auth
import google.auth.transport.requests
import requests

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://www.googleapis.com/webmasters/v3/sites"


class GscError(RuntimeError):
    pass


def _headers() -> dict:
    try:
        creds, project = google.auth.default(scopes=[SCOPE])
        creds.refresh(google.auth.transport.requests.Request())
    except Exception as e:
        raise GscError(f"ADC 인증 실패 — gcloud auth application-default login 필요: {e}")
    h = {"Authorization": f"Bearer {creds.token}"}
    if project:
        h["x-goog-user-project"] = project
    return h


def sites(headers: dict | None = None) -> list[str]:
    r = requests.get(API, headers=headers or _headers(), timeout=30)
    if r.status_code != 200:
        raise GscError(f"사이트 목록 {r.status_code}: {r.text[:200]}")
    return [s["siteUrl"] for s in r.json().get("siteEntry", [])]


def query(site: str, start: str, end: str, dimensions: list[str], headers: dict | None = None,
          row_limit: int = 25000) -> list[dict]:
    """searchAnalytics.query — 행마다 {keys: {차원: 값}, clicks, impressions, ctr, position}."""
    headers = headers or _headers()
    out, start_row = [], 0
    while True:
        r = requests.post(f"{API}/{quote(site, safe='')}/searchAnalytics/query", headers=headers, timeout=60,
                          json={"startDate": start, "endDate": end, "dimensions": dimensions,
                                "rowLimit": row_limit, "startRow": start_row, "dataState": "all"})
        if r.status_code != 200:
            raise GscError(f"{site} 검색 실적 {r.status_code}: {r.text[:200]}")
        rows = r.json().get("rows", [])
        for row in rows:
            out.append({"keys": dict(zip(dimensions, row["keys"])), "clicks": int(row["clicks"]),
                        "impressions": int(row["impressions"]), "position": float(row["position"])})
        if len(rows) < row_limit:
            return out
        start_row += row_limit
