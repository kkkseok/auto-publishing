"""Threads 발행 사전 승인 — Meta 플랫폼 약관 7.e.i.2 시정 조치.

무인 자동 게시를 중단하고, 모든 Threads 게시물이 발행 전에 텔레그램으로
운영자에게 전달되어 **승인한 건만** 게시되도록 한다.

구조 (프로세스 간 통신이 필요한 이유):

    파이프라인은 scheduler_runner 가 띄우는 **별도 subprocess** 이고,
    텔레그램 응답을 받는 long-poll 스레드는 **스케줄러 프로세스** 안에 있다.
    (pipelines/tistory_bridge.py 의 _telegram_long_poll_loop — DKAPTCHA 답변
    수신용으로 이미 상시 동작 중)

    따라서 캡차처럼 in-memory dict 로는 오갈 수 없고, 파일을 매개로 한다:

        파이프라인 subprocess              스케줄러 프로세스 (bridge)
        ─────────────────────              ──────────────────────────
        request()                          _telegram_long_poll_loop
          └ 텔레그램 전송(force_reply)       └ 답글 수신
          └ data/threads_approvals.json  ←──┘ resolve_by_tg_message_id()
             에 pending 기록
        wait() — 파일 폴링
          └ approved → 발행 / 그 외 → 미발행

    getUpdates 는 offset 으로 확정되면 다른 소비자가 같은 업데이트를 볼 수
    없다. 그래서 파이프라인이 별도로 getUpdates 를 돌리면 브릿지의 캡차 수신을
    훔쳐간다 — 브릿지가 살아있는 동안에는 절대 직접 폴링하지 않고 파일만 본다.
    브릿지가 없을 때(수동 실행 등)만 자체 폴링으로 폴백한다.

승인 규칙:
    답글이 승인 키워드면 발행, 그 외 텍스트는 거부로 본다. 무응답은 발행하지
    않는다(fail-closed) — 승인 없이 게시되는 경로를 남기지 않기 위함이다.

환경변수:
    THREADS_APPROVAL_REQUIRED   false이면 발행 중지 (승인 우회 불가)
    THREADS_APPROVAL_TIMEOUT    승인 대기 초 (기본 600)
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from common.logger import log

_PATH = Path(__file__).resolve().parent.parent / "data" / "threads_approvals.json"

# 승인 키워드에 정확히 일치할 때만 발행한다. 나머지는 전부 거부로 본다 —
# "잠깐만", "이건 좀" 같은 답글을 승인으로 오독하는 경로를 남기지 않기 위함.
_APPROVE_WORDS = {"ok", "o", "y", "yes", "예", "승인", "발행", "확인", "ㅇ", "👍"}

# 브릿지 생존 확인 — 살아있으면 절대 자체 getUpdates 를 돌리지 않는다.
_BRIDGE_HEALTH = "http://127.0.0.1:5757/healthz"


def required() -> bool:
    raw = str(os.getenv("THREADS_APPROVAL_REQUIRED", "")).strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off")


def _timeout_sec() -> int:
    try:
        return int(str(os.getenv("THREADS_APPROVAL_TIMEOUT", "")).strip() or 600)
    except ValueError:
        return 600


def _fmt_wait(sec: int) -> str:
    """대기 시간 표기 — 60초 미만을 '0분' 으로 쓰지 않기 위함."""
    return f"{sec // 60}분" if sec >= 60 else f"{sec}초"


# ──────────────────────────────────────────────────────────────────────────────
# 상태 파일 (atomic write — 두 프로세스가 같은 파일을 만진다)
# ──────────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    if not _PATH.exists():
        return {}
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def _prune(data: dict) -> dict:
    """24시간 지난 기록 제거 — 파일 무한 증가 방지."""
    cutoff = datetime.now() - timedelta(hours=24)
    for aid in list(data.keys()):
        try:
            if datetime.fromisoformat(data[aid].get("created_at", "")) < cutoff:
                del data[aid]
        except Exception:
            del data[aid]
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 텔레그램
# ──────────────────────────────────────────────────────────────────────────────

def _send_request_message(text: str, meta: str) -> Optional[int]:
    """승인 요청 메시지 발송. message_id 반환 (실패 시 None)."""
    try:
        import requests
    except ImportError:
        log("[threads_approval] requests 미설치 — 승인 요청 불가", "error")
        return None

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("[threads_approval] TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 승인 요청 불가",
            "error")
        return None

    body = (
        f"🧵 Threads 발행 승인 요청\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{meta}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{text}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"이 메시지에 답글로 승인/거부하세요.\n"
        f"  승인: ok / 승인 / o\n"
        f"  거부: no / 거부 / x\n"
        f"※ {_fmt_wait(_timeout_sec())} 내 무응답 시 발행하지 않습니다."
    )
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": body[:4096],
                "disable_web_page_preview": True,
                "reply_markup": {
                    "force_reply": True,
                    "input_field_placeholder": "ok / no",
                },
            },
            timeout=15,
        )
        if not r.ok:
            log(f"[threads_approval] sendMessage {r.status_code}: {r.text[:200]}",
                "error")
            return None
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        log(f"[threads_approval] sendMessage 예외: {e}", "error")
        return None


def _bridge_alive() -> bool:
    try:
        import requests
        return requests.get(_BRIDGE_HEALTH, timeout=2).ok
    except Exception:
        return False


def _decide(reply_text: str) -> str:
    """답글 텍스트 → approved / rejected."""
    t = (reply_text or "").strip().lower()
    t = re.sub(r"[.!\s]+$", "", t)
    if t in _APPROVE_WORDS:
        return "approved"
    return "rejected"


# ──────────────────────────────────────────────────────────────────────────────
# 파이프라인 측 API
# ──────────────────────────────────────────────────────────────────────────────

def request(text: str, meta: str = "") -> Optional[str]:
    """승인 요청 등록 + 텔레그램 발송. approval_id 반환 (실패 시 None)."""
    msg_id = _send_request_message(text, meta)
    if not msg_id:
        return None

    aid = uuid.uuid4().hex[:12]
    data = _prune(_load())
    data[aid] = {
        "tg_message_id": msg_id,
        "tg_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "status":        "pending",
        "meta":          meta,
        "preview":       text[:200],
        "created_at":    datetime.now().isoformat(timespec="seconds"),
        "decided_at":    "",
        "reply":         "",
    }
    _save(data)
    log(f"[threads_approval] 승인 요청 발송 (id={aid}, tg_msg={msg_id})", "step")
    return aid


def wait(approval_id: str, timeout_sec: Optional[int] = None) -> tuple[bool, str]:
    """승인 결과 대기. (승인여부, 사유) 반환.

    브릿지가 살아있으면 파일만 폴링한다. 브릿지가 없으면(수동 실행 등) 자체
    getUpdates 로 폴백한다 — 이때는 훔쳐갈 상대가 없으므로 충돌하지 않는다.
    """
    timeout_sec = timeout_sec or _timeout_sec()
    deadline = time.time() + timeout_sec
    use_self_poll = not _bridge_alive()
    if use_self_poll:
        log("[threads_approval] 브릿지 미가동 — 자체 폴링으로 승인 대기", "info")

    offset = 0
    while time.time() < deadline:
        data = _load()
        rec = data.get(approval_id)
        if not rec:
            return False, "승인 요청 기록이 사라짐"
        if rec.get("status") == "approved":
            return True, ""
        if rec.get("status") == "rejected":
            return False, f"운영자 거부 (답글: {rec.get('reply', '')[:30]})"

        if use_self_poll:
            offset = _self_poll_once(offset)

        time.sleep(3)

    _mark(approval_id, "timeout", "")
    return False, f"{_fmt_wait(timeout_sec)} 내 승인 없음 — 발행하지 않음"


def _self_poll_once(offset: int) -> int:
    """브릿지가 없을 때만 쓰는 getUpdates 1회. 새 offset 반환."""
    try:
        import requests
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return offset
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"offset": offset, "timeout": 0}, timeout=10)
        if not r.ok:
            return offset
        for upd in r.json().get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            reply_to = msg.get("reply_to_message")
            if not reply_to:
                continue
            resolve_by_tg_message_id(reply_to.get("message_id"),
                                     (msg.get("text") or "").strip(),
                                     chat_id=msg.get("chat", {}).get("id"))
    except Exception:
        pass
    return offset


# ──────────────────────────────────────────────────────────────────────────────
# 브릿지 측 API
# ──────────────────────────────────────────────────────────────────────────────

def find_by_tg_message_id(msg_id: int) -> Optional[str]:
    """텔레그램 message_id 로 pending 승인 요청 찾기."""
    if not msg_id:
        return None
    for aid, rec in _load().items():
        if rec.get("tg_message_id") == msg_id and rec.get("status") == "pending":
            return aid
    return None


def resolve_by_tg_message_id(msg_id: int, reply_text: str, *, chat_id=None) -> bool:
    """답글 수신 → 승인/거부 확정. 처리했으면 True.

    브릿지의 텔레그램 long-poll 이 호출한다. 캡차 답변과 같은 채널을 쓰므로,
    매칭되는 pending 승인 요청이 없으면 False 를 반환해 호출자가 캡차 처리로
    넘어가게 한다.
    """
    aid = find_by_tg_message_id(msg_id)
    if not aid:
        return False
    rec = _load().get(aid, {})
    expected_chat = str(rec.get("tg_chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    if not expected_chat or str(chat_id) != expected_chat:
        return False
    status = _decide(reply_text)
    _mark(aid, status, reply_text)
    log(f"[threads_approval] {status} (id={aid}, 답글={reply_text[:20]})", "ok")
    return True


def _mark(approval_id: str, status: str, reply_text: str) -> None:
    data = _load()
    rec = data.get(approval_id)
    if not rec:
        return
    rec["status"] = status
    rec["reply"] = reply_text
    rec["decided_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)


# ──────────────────────────────────────────────────────────────────────────────
# 통합 진입점
# ──────────────────────────────────────────────────────────────────────────────

def gate(text: str, meta: str = "") -> tuple[bool, str]:
    """발행 직전 승인 게이트. (통과여부, 사유) 반환."""
    if not required():
        return False, "승인 기능 비활성화 — 승인 없이 발행할 수 없음"
    aid = request(text, meta)
    if not aid:
        # 승인 요청을 보내지 못하면 발행하지 않는다. 여기서 통과시키면
        # '텔레그램이 죽으면 무인 발행으로 되돌아가는' 우회로가 생긴다.
        return False, "승인 요청 발송 실패 — 발행하지 않음"
    return wait(aid)
