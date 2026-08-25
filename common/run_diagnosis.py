"""
파이프라인 실행 실패의 원인 분류.

run_ledger 의 record 를 받아 흔한 실패 패턴을 매칭하고 사람이 읽기 좋은
원인 라벨을 반환한다. 매칭 실패 시 stderr 마지막 줄을 그대로 노출해 사용자가
원본 메시지로 판단할 수 있게 한다.

매핑 우선순위 (위에서부터 먼저 매칭):
  1. data.go.kr 인증키 401/등록되지 않은 인증키
  2. Kakao/티스토리 세션 만료 (auth/login 리다이렉트, Kakao 로그인 실패)
  3. Naver 봇 탐지/로그인 차단
  4. Playwright timeout / browser launch 실패
  5. Threads/Twitter rate-limit
  6. subprocess timeout
  7. (폴백) stderr 의 마지막 의미 있는 한 줄
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Diagnosis:
    label: str   # 짧은 한국어 라벨 (텔레그램 1줄)
    hint: str    # 사용자 조치 힌트 (선택; 비어 있을 수 있음)


# (정규식, 라벨, 조치 힌트) 의 우선순위 리스트.
# stderr_tail 전체에 대해 search 수행.
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"등록되지\s*않은\s*인증키|DATA_GO_KR_KEY\s*env\s*누락", re.I),
        "data.go.kr 인증키 폐기/만료",
        ".env DATA_GO_KR_KEY 재발급 후 교체",
    ),
    (
        re.compile(r"data\.go\.kr.*?(401|Unauthorized)", re.I | re.S),
        "data.go.kr 401 (인증키 거부)",
        "data.go.kr 마이페이지에서 활용 상태 확인 / 재발급",
    ),
    (
        # 뉴스픽 전용 — tistory 패턴보다 먼저 매칭돼야 올바른 복구 명령을 안내한다.
        re.compile(r"뉴스픽\s*세션\s*없음|Kakao\s*SSO\s*로그인\s*실패|newspic", re.I),
        "뉴스픽 Kakao 세션 만료",
        "python tools/newspick_manual_login.py 로 수동 로그인",
    ),
    (
        # 알리 전용 — 제휴 세션 만료. 일반 '수동 로그인 필요'(아래)와 키워드
        # 미매칭(맨 아래)보다 먼저 매칭돼야 정확한 복구 명령을 안내한다.
        re.compile(r"알리\s*제휴\s*세션\s*만료|aliexpress_manual_login", re.I),
        "알리 제휴 세션 만료",
        "python tools/aliexpress_manual_login.py → 'Continue with Google' 로 로그인",
    ),
    (
        # 쿠팡 OpenAPI 자격 회귀 — data.go.kr 401 패턴과 구분되도록 쿠팡 문구로 한정.
        re.compile(r"Invalid\s*signature|쿠팡\s*stats\s*조회\s*실패|"
                   r"SECRET_KEY\s*길이\s*이상|쿠팡\s*\w*\s*리포트\s*실패:\s*40[13]", re.I),
        "쿠팡 OpenAPI 서명 거부 (키 재발급/재복사 필요)",
        ".env COUPANG_ACCESS_KEY/SECRET_KEY 교체 후 python -m tools.coupang_source_roi 로 확인",
    ),
    (
        # 발행 게이트가 의도대로 막은 경우 — 장애가 아니다. 발행 0건이라 ledger 는
        # failure 로 잡히지만, 원인 라벨은 '정상 차단'임이 드러나야 한다.
        # (Meta 7.e.i.2 시정 조치, docs/meta-threads-appeal-2026-08.md)
        re.compile(r"발행\s*승인\s*없음|승인\s*요청\s*발송\s*실패|"
                   r"운영자\s*거부|분\s*내\s*승인\s*없음", re.I),
        "Threads 발행 미승인 (사람 승인 게이트)",
        "텔레그램 승인 요청에 답글(ok) — 무응답 시 발행하지 않음이 정상 동작",
    ),
    (
        re.compile(r"발행\s*게이트\s*차단|일일\s*상한\s*\d+건\s*도달|"
                   r"제휴\s*비중\s*\d+%\s*>\s*상한", re.I),
        "Threads 발행량 상한 도달 (정상 차단)",
        "조치 불필요 — THREADS_MAX_* 로 조정 가능",
    ),
    (
        # AI 캡션 실패 → 폴백 발행 금지로 취소. thin content 게이트와 같은 성격.
        re.compile(r"AI\s*캡션\s*생성\s*실패|폴백\s*금지", re.I),
        "Threads AI 캡션 생성 실패 (폴백 발행 차단)",
        "ANTHROPIC_API_KEY / GEMINI_API_KEY 상태 확인 — 다음 슬롯에서 자동 재시도",
    ),
    (
        # Threads 앱 사망 — Meta 가 앱의 API 접근을 끈 상태. 토큰 재발급으로는
        # 복구되지 않는다(액세스 토큰 없이 app_id+secret 만 쓰는 호출도 거부됨).
        # 아래 'Threads 토큰 만료'/rate-limit 과 원인·조치가 전혀 달라 먼저 매칭한다.
        # 2026-08-18~08-25 실측: 전 Threads 채널 70건 연속 실패가 폴백 라벨
        # '컨테이너 생성 실패' 로만 보여 일주일간 원인이 드러나지 않았다.
        re.compile(r"API\s*access\s*deactivated", re.I),
        "Threads 앱 API 접근 차단 (Meta 앱 비활성)",
        "developers.facebook.com 앱 상태 확인 — 토큰 재발급으로는 복구 불가",
    ),
    (
        # 위와 달리 이쪽은 재발급으로 풀린다. code 190 은 다른 플랫폼에도 나오므로
        # 같은 stderr 안에 Threads 문구가 있을 때만 Threads 로 단정한다.
        re.compile(r"Threads[\s\S]{0,300}?(Session\s*has\s*expired|"
                   r"Invalid\s*OAuth\s*access\s*token|\"code\"\s*:\s*190)", re.I),
        "Threads 액세스 토큰 만료",
        "python -m common.threads_token oauth 로 재발급",
    ),
    (
        re.compile(r"/auth/login|Kakao\s*로그인\s*실패|Kakao\s*페이지\s*전환\s*실패", re.I),
        "Kakao(티스토리) 세션 만료",
        "python -m tools.verify_tistory_login <blog>",
    ),
    (
        re.compile(r"수동\s*로그인\s*필요|notify_login_required", re.I),
        "수동 로그인 가드 발동",
        "텔레그램의 instructions 명령 실행",
    ),
    (
        re.compile(r"naver.*?(login|로그인).*?(차단|차단됨|실패|봇)", re.I | re.S),
        "Naver 로그인 차단/봇 탐지",
        "python tools/naver_manual_login.py 로 수동 로그인",
    ),
    (
        # thin content 게이트 — AI 생성(도입부/가이드/뉴스픽 본문) 실패로 본문이
        # 기준 미달이면 발행을 차단한다. 네트워크 패턴보다 먼저 매칭해야
        # 'AI 실패 → 발행 차단' 이라는 실제 조치 대상이 라벨에 드러난다.
        re.compile(r"thin\s*content\s*방지", re.I),
        "AI 본문 생성 실패 (thin content 발행 차단)",
        "ANTHROPIC_API_KEY / GEMINI_API_KEY 상태 확인 — 다음 슬롯에서 자동 재시도",
    ),
    (
        re.compile(r"Threads.*?rate.?limit|status.*?429", re.I),
        "Threads/SNS API rate-limit",
        "잠시 대기 후 재시도 (자동 회복)",
    ),
    (
        re.compile(r"subprocess\s*timeout\s*\d+s|TimeoutError", re.I),
        "subprocess 타임아웃",
        "SCHEDULE_SUBPROCESS_TIMEOUT 상향 또는 외부 응답 지연 확인",
    ),
    (
        re.compile(r"playwright.*?(Timeout|Error)|browser.*?(launch|context).*?failed", re.I),
        "Playwright 브라우저 오류",
        "Chromium 재설치 / orphan 프로세스 정리",
    ),
    (
        re.compile(r"ConnectionError|ConnectTimeout|Max retries exceeded|requests\.exceptions", re.I),
        "외부 네트워크 오류",
        "일시적 — 다음 슬롯에 자동 회복 가능",
    ),
    (
        # 알리 수집 0건 — 명품/한국 고유명사 등 알리 부적합 키워드. 진짜 장애가
        # 아니므로 네트워크/Playwright 패턴 뒤에 둬 그것들이 먼저 매칭되게 한다.
        re.compile(r"수집\s*0건|상품/제휴링크\s*수집|상품/링크\s*수집\s*실패|키워드\s*부적합|매칭\s*부족", re.I),
        "알리 키워드 미매칭 (검색 0건)",
        "해당 키워드 풀에서 자동 제외됨 — 조치 불필요 (자가 치유)",
    ),
]


def _last_meaningful_line(text: str) -> str:
    """stderr 의 마지막 의미있는 한 줄 (공백/INFO 제외)."""
    if not text:
        return ""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if not s:
            continue
        # ANSI 색상코드 제거
        s = re.sub(r"\x1b\[[0-9;]*m", "", s)
        if not s:
            continue
        return s[:200]
    return ""


def diagnose(record: dict) -> Diagnosis:
    """run_ledger record 를 받아 Diagnosis 반환."""
    status = record.get("status", "")
    if status == "success":
        return Diagnosis(label="성공", hint="")

    stderr = record.get("stderr_tail") or ""
    error  = record.get("error") or ""
    haystack = stderr + "\n" + error

    if status == "timeout":
        return Diagnosis(
            label="subprocess 타임아웃",
            hint="SCHEDULE_SUBPROCESS_TIMEOUT 상향 또는 외부 응답 지연 확인",
        )

    for pat, label, hint in _PATTERNS:
        if pat.search(haystack):
            return Diagnosis(label=label, hint=hint)

    # 폴백 — stderr 마지막 의미있는 한 줄
    tail = _last_meaningful_line(stderr) or _last_meaningful_line(error)
    if tail:
        return Diagnosis(label=tail, hint="")
    return Diagnosis(label=f"원인 불명 (exit={record.get('exit_code')})", hint="")
