"""Threads 발행 게이트 — Meta 플랫폼 약관 7.e.i.2 시정 조치.

2026-08-18 Meta 가 앱(THREADS_APP_ID)의 API 접근을 차단했다. 사유는 플랫폼 약관
7.e.i.2 — "플랫폼·제품·데이터·사용자에게 부정적 영향". 포괄 조항이라 Meta 는
구체적 행위를 지목하지 않았고, 실측(473건, 2026-05-14~08-17)에서 드러난 개연성
높은 원인은 셋이었다.

  1. 자동 게시 슬롯 하루 11회 (뉴스픽 4 + 쿠팡 4 + 백링크 2 + 알리 1)
  2. 제휴 링크 포함 게시물이 전체의 57.3% (271/473)
  3. AI 생성 실패 시 폴백 발행 — 뉴스픽은 원문 기사 제목을 그대로 내보내
     한국어 계정에 베트남어/영어 게시물이 섞였고, 쿠팡은 상품명만 덜렁 나갔다

이 모듈은 2·3을 코드에 내장된 게이트로 막는다. 1은 .env 스케줄로 처리한다.

**게이트를 publishers/threads.py 진입점에 두는 이유**: 파이프라인마다 넣으면
새 파이프라인이 누락시킬 수 있다. Publisher 진입점은 모든 Threads 발행이
반드시 통과하는 유일한 지점이라, 설정값을 되돌리는 것만으로는 우회되지 않는다
(Meta 소명의 "코드에 내장" 근거).

환경변수:
    THREADS_MAX_PER_DAY            하루 총 게시물 상한 (기본 3)
    THREADS_MAX_AFFILIATE_PER_DAY  제휴 게시물 하루 상한 (기본 1)
    THREADS_MAX_AFFILIATE_RATIO    최근 30일 제휴 비중 상한 (기본 0.30)
    THREADS_MIN_CHARS              실질 본문 최소 길이 (기본 80)
    THREADS_REQUIRE_KOREAN         한국어 이외 차단 (기본 true)
    THREADS_MIN_KOREAN_RATIO       한글 최소 비율 (기본 0.30)
    THREADS_GUARD_ENABLED          false이면 발행 중지 (게이트 우회 불가)

CLI:
    python -m common.threads_guard          # 오늘 소진 현황
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from common.logger import log

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "threads_guard.json"

# 비중 계산 창 — 하루치만 보면 "오늘 1건이 제휴" 가 곧 100% 라 상한이 무의미해진다.
_RATIO_WINDOW_DAYS = 30

# 실질 본문 길이/언어 판정에서 제외할 요소.
#   - 의무 고지문: 전 제휴 게시물에 동일하게 붙어 길이·한글비율을 인위적으로 올린다
#   - URL / 해시태그 / 이모지: 본문 실질이 아니다
_NOTICE_RE = re.compile(
    r"\[?광고\]?\s*이\s*게시물은.*?활동의\s*일환으로.*?제공받습니다\.?", re.S)
_URL_RE = re.compile(r"https?://\S+")
_TAG_RE = re.compile(r"#\S+")
_NON_TEXT_RE = re.compile(
    r"[\U0001F000-\U0001FAFF☀-➿️←-⇿\s]+")
_HANGUL_RE = re.compile(r"[가-힣]")


# ──────────────────────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────────────────────

def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip() or default)
        return value if math.isfinite(value) else default
    except ValueError:
        return default


def _bool_env(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def enabled() -> bool:
    return _bool_env("THREADS_GUARD_ENABLED", True)


# ──────────────────────────────────────────────────────────────────────────────
# 상태 파일
# ──────────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("발행량 기록 형식 오류")
        for day, rec in data.items():
            datetime.strptime(day, "%Y-%m-%d")
            if (not isinstance(rec, dict)
                    or type(rec.get("total")) is not int
                    or type(rec.get("affiliate")) is not int
                    or not 0 <= rec["affiliate"] <= rec["total"]):
                raise ValueError("발행량 기록 값 오류")
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Threads 발행량 기록을 읽을 수 없어 발행 중지") from exc


def _save(data: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STATE_PATH)


@contextmanager
def publication_lock():
    """Serialize quota check, approval and publish across local processes.

    SQLite releases the lock on process exit. A competing publisher fails closed.
    """
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_STATE_PATH.with_suffix(".lock.sqlite3")), timeout=1)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
    finally:
        conn.rollback()
        conn.close()


def _today() -> str:
    return date.today().isoformat()


def _window_totals(data: dict, days: int = _RATIO_WINDOW_DAYS) -> tuple[int, int]:
    """최근 days 일의 (총 게시물, 제휴 게시물) 합계."""
    cutoff = date.today() - timedelta(days=days - 1)
    total = affiliate = 0
    for day, rec in data.items():
        try:
            if not cutoff <= datetime.strptime(day, "%Y-%m-%d").date() <= date.today():
                continue
        except ValueError:
            continue
        total += int(rec.get("total", 0) or 0)
        affiliate += int(rec.get("affiliate", 0) or 0)
    return total, affiliate


def today_usage() -> dict:
    """오늘 소진 현황 — 진단/CLI 용."""
    data = _load()
    rec = data.get(_today(), {})
    win_total, win_aff = _window_totals(data)
    return {
        "date":            _today(),
        "total":           int(rec.get("total", 0) or 0),
        "affiliate":       int(rec.get("affiliate", 0) or 0),
        "max_total":       min(3, max(0, _int_env("THREADS_MAX_PER_DAY", 3))),
        "max_affiliate":   min(1, max(0, _int_env("THREADS_MAX_AFFILIATE_PER_DAY", 1))),
        "window_total":    win_total,
        "window_affiliate": win_aff,
        "window_ratio":    (win_aff / win_total) if win_total else 0.0,
        "max_ratio":       min(0.30, max(0.0, _float_env("THREADS_MAX_AFFILIATE_RATIO", 0.30))),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 품질 검사
# ──────────────────────────────────────────────────────────────────────────────

def _substantive_text(text: str) -> str:
    """의무 고지문·URL·해시태그·이모지를 걷어낸 실질 본문."""
    t = _NOTICE_RE.sub(" ", text or "")
    t = _URL_RE.sub(" ", t)
    t = _TAG_RE.sub(" ", t)
    return _NON_TEXT_RE.sub(" ", t).strip()


def check_quality(text: str) -> tuple[bool, str]:
    """본문 품질 검사. (통과여부, 사유) 반환.

    AI 생성이 실패했을 때 파이프라인이 상품명·원문 기사 제목만으로 폴백 발행하던
    경로를 여기서 끊는다. 실측상 이 폴백이 저품질 게시물과 언어 불일치 게시물
    (한국어 계정에 베트남어/영어 원문) 양쪽의 실제 원인이었다.
    """
    body = _substantive_text(text)

    min_chars = max(80, _int_env("THREADS_MIN_CHARS", 80))
    if len(body) < min_chars:
        return False, f"실질 본문 {len(body)}자 < 최소 {min_chars}자 (AI 생성 실패 폴백 의심)"

    if not _bool_env("THREADS_REQUIRE_KOREAN", True):
        return False, "한국어 품질 검사 비활성화 — 발행 중지"
    if body:
        letters = [c for c in body if c.isalpha()]
        if letters:
            ratio = len(_HANGUL_RE.findall(body)) / len(letters)
            min_ratio = max(0.30, _float_env("THREADS_MIN_KOREAN_RATIO", 0.30))
            if ratio < min_ratio:
                return False, (f"한글 비율 {ratio:.0%} < {min_ratio:.0%} — "
                               f"계정 주 언어(한국어)와 불일치")

    return True, ""


# ──────────────────────────────────────────────────────────────────────────────
# 발행량 검사
# ──────────────────────────────────────────────────────────────────────────────

def check_quota(is_affiliate: bool) -> tuple[bool, str]:
    """일일 상한·제휴 비중 검사. (통과여부, 사유) 반환."""
    try:
        data = _load()
    except ValueError as exc:
        return False, str(exc)
    rec = data.get(_today(), {})
    total = int(rec.get("total", 0) or 0)
    aff = int(rec.get("affiliate", 0) or 0)

    max_total = min(3, max(0, _int_env("THREADS_MAX_PER_DAY", 3)))
    if total >= max_total:
        return False, f"오늘 게시물 {total}건 — 일일 상한 {max_total}건 도달"

    if is_affiliate:
        max_aff = min(1, max(0, _int_env("THREADS_MAX_AFFILIATE_PER_DAY", 1)))
        if aff >= max_aff:
            return False, f"오늘 제휴 게시물 {aff}건 — 일일 상한 {max_aff}건 도달"

        win_total, win_aff = _window_totals(data)
        max_ratio = min(0.30, max(0.0, _float_env("THREADS_MAX_AFFILIATE_RATIO", 0.30)))
        projected = (win_aff + 1) / (win_total + 1)
        if projected > max_ratio:
            return False, (f"제휴 비중 {projected:.0%} > 상한 {max_ratio:.0%} "
                           f"(최근 {_RATIO_WINDOW_DAYS}일 {win_aff}/{win_total})")

    return True, ""


def record_published(is_affiliate: bool) -> None:
    """발행 성공을 카운터에 반영."""
    data = _load()
    rec = data.setdefault(_today(), {"total": 0, "affiliate": 0})
    rec["total"] = int(rec.get("total", 0) or 0) + 1
    if is_affiliate:
        rec["affiliate"] = int(rec.get("affiliate", 0) or 0) + 1

    # 창 밖 기록은 비중 계산에 쓰이지 않으므로 정리한다 (파일 무한 증가 방지).
    cutoff = date.today() - timedelta(days=_RATIO_WINDOW_DAYS * 2)
    for day in list(data.keys()):
        try:
            if datetime.strptime(day, "%Y-%m-%d").date() < cutoff:
                del data[day]
        except ValueError:
            del data[day]
    _save(data)


# ──────────────────────────────────────────────────────────────────────────────
# 통합 진입점
# ──────────────────────────────────────────────────────────────────────────────

def is_affiliate_text(text: str) -> bool:
    """본문이 상업(제휴) 게시물인지 판정.

    고지문 존재 여부로 보는 게 가장 확실하다 — 제휴 링크가 들어가는 모든 경로가
    common/affiliate_notice.py 를 통과하기 때문이다. 단축 URL 이 섞여 링크 도메인
    으로는 판정할 수 없는 케이스도 고지문으로는 잡힌다.
    """
    if not text:
        return False
    low = text.lower()
    if "affiliate-notice" in low or "파트너스 활동의 일환" in text:
        return True
    return any(d in low for d in (
        "coupang.com", "coupa.ng", "link.coupang",
        "aliexpress.com", "s.click.aliexpress",
    ))


def precheck(text: str) -> tuple[bool, str, bool]:
    """발행 직전 통합 검사.

    Returns:
        (통과여부, 차단사유, 제휴여부)
    """
    affiliate = is_affiliate_text(text)
    if not enabled():
        return False, "품질·발행량 검사 비활성화 — 발행 중지", affiliate

    ok, reason = check_quality(text)
    if not ok:
        return False, reason, affiliate

    ok, reason = check_quota(affiliate)
    if not ok:
        return False, reason, affiliate

    return True, "", affiliate


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    u = today_usage()
    print("=== Threads 발행 게이트 현황 ===")
    print(f"  활성화       : {enabled()}")
    print(f"  오늘({u['date']})")
    print(f"    총 게시물  : {u['total']} / {u['max_total']}")
    print(f"    제휴       : {u['affiliate']} / {u['max_affiliate']}")
    print(f"  최근 {_RATIO_WINDOW_DAYS}일")
    print(f"    총 게시물  : {u['window_total']}")
    print(f"    제휴 비중  : {u['window_ratio']:.1%} (상한 {u['max_ratio']:.0%})")


if __name__ == "__main__":
    main()
