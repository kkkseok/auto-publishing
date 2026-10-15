"""네이버 검색광고 키워드 도구 API — 월간 검색량·경쟁도 (공식 출처).

키: .env NAVER_SEARCHAD_API_KEY(액세스라이선스) / NAVER_SEARCHAD_SECRET_KEY(비밀키) /
NAVER_SEARCHAD_CUSTOMER_ID. 광고 집행 없이 발급 가능.
서명: base64(HMAC-SHA256(secret, "{timestamp}.{METHOD}.{uri}")).
"""
import base64
import hashlib
import hmac
import os
import time

import requests

from common.logger import log

_BASE = "https://api.searchad.naver.com"


def _headers(method: str, uri: str) -> dict:
    ts = str(int(time.time() * 1000))
    secret = os.getenv("NAVER_SEARCHAD_SECRET_KEY", "")
    sig = base64.b64encode(hmac.new(secret.encode(), f"{ts}.{method}.{uri}".encode(),
                                    hashlib.sha256).digest()).decode()
    return {"X-Timestamp": ts, "X-API-KEY": os.getenv("NAVER_SEARCHAD_API_KEY", ""),
            "X-Customer": os.getenv("NAVER_SEARCHAD_CUSTOMER_ID", ""), "X-Signature": sig}


def _count(v) -> int:
    # 검색량이 적으면 "< 10" 문자열로 온다.
    if isinstance(v, (int, float)):
        return int(v)
    return 5 if isinstance(v, str) and v.strip().startswith("<") else 0


def related_keywords(hints: list[str]) -> list[dict] | None:
    """힌트 키워드(최대 5개)와 연관 키워드의 월간 검색량. 실패면 None.

    반환 행: {keyword, pc, mobile, total, comp(낮음|중간|높음), ad_depth}
    """
    hints = [h.replace(" ", "") for h in hints if h.strip()][:5]
    if not hints:
        return []
    uri = "/keywordstool"
    try:
        r = requests.get(_BASE + uri, headers=_headers("GET", uri), timeout=15,
                         params={"hintKeywords": ",".join(hints), "showDetail": "1"})
    except requests.RequestException as e:
        log(f"[검색광고] 요청 오류: {e}", "error")
        return None
    if not r.ok:
        log(f"[검색광고] HTTP {r.status_code} {r.text[:150]}", "error")
        return None
    rows = []
    for k in r.json().get("keywordList", []):
        pc, mo = _count(k.get("monthlyPcQcCnt")), _count(k.get("monthlyMobileQcCnt"))
        rows.append({"keyword": k.get("relKeyword", ""), "pc": pc, "mobile": mo, "total": pc + mo,
                     "comp": k.get("compIdx", ""), "ad_depth": k.get("plAvgDepth", 0)})
    return rows
