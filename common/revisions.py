"""기존 글 수정안 기록 + 텔레그램 승인 — data/revisions.json.

pipelines/content_revise 가 유입 데이터로 고를 만한 글(content_insights.revision_candidates)의
수정안을 만들어 여기에 proposed 로 남기고 텔레그램으로 보낸다. 사용자가 그 메시지에
'승인'(또는 ok·예·ㅇ) 답장을 하면 approved, 다른 답장은 rejected.

발행된 글을 바꾸면 순위가 흔들릴 수 있어 처음엔 사람 승인을 거친다 (2026-10-19).
답장은 스케줄러 프로세스의 브릿지 long-poll(pipelines/tistory_bridge)이 받아
resolve_by_tg_message_id() 를 부른다 — threads_approval 과 같은 구조.

상태: proposed → approved | rejected → applied | apply_failed
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import requests

from common.logger import log

PATH = Path(__file__).resolve().parent.parent / "data" / "revisions.json"
_LOCK = threading.Lock()
_APPROVE = {"ok", "o", "y", "yes", "예", "승인", "반영", "확인", "ㅇ", "👍"}


def _load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def all_() -> dict:
    return _load()


def pending_urls() -> set[str]:
    return {r["url"] for r in _load().values() if r.get("status") in ("proposed", "approved")}


def by_status(status: str) -> list[tuple[str, dict]]:
    return sorted(((k, v) for k, v in _load().items() if v.get("status") == status),
                  key=lambda kv: kv[1].get("created", ""))


def add(**rec) -> str:
    rid = uuid.uuid4().hex[:10]
    with _LOCK:
        data = _load()
        data[rid] = dict(rec, status="proposed", created=datetime.now().isoformat(timespec="seconds"))
        _save(data)
    return rid


def update(rid: str, **fields) -> None:
    with _LOCK:
        data = _load()
        if rid in data:
            data[rid].update(fields, updated=datetime.now().isoformat(timespec="seconds"))
            _save(data)


def send_for_approval(rid: str, text: str) -> bool:
    """수정 제안을 텔레그램으로 보낸다 (답장 입력창이 바로 뜨게 force_reply). 실패하면 False."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("[수정안] TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 승인 요청 불가", "error")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json={
            "chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True,
            "reply_markup": {"force_reply": True, "input_field_placeholder": "승인 / 다른 답장은 보류"}})
        msg_id = r.json().get("result", {}).get("message_id") if r.ok else None
    except requests.RequestException as e:
        log(f"[수정안] 텔레그램 예외: {e}", "error")
        return False
    if not msg_id:
        log(f"[수정안] 텔레그램 {r.status_code}: {r.text[:200]}", "error")
        return False
    update(rid, tg_message_id=msg_id, tg_chat_id=chat_id)
    return True


def _decide(text: str) -> str:
    t = re.sub(r"[.!\s]+$", "", (text or "").strip().lower())
    return "approved" if t in _APPROVE else "rejected"


def resolve_by_tg_message_id(msg_id: int, reply_text: str, *, chat_id=None) -> bool:
    """브릿지 long-poll 이 부른다 — 이 메시지가 수정 제안이면 승인/거부를 기록하고 True."""
    for rid, rec in _load().items():
        if rec.get("tg_message_id") != msg_id or rec.get("status") != "proposed":
            continue
        if str(chat_id) != str(rec.get("tg_chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")).strip():
            return False
        status = _decide(reply_text)
        update(rid, status=status, reply=reply_text[:100], decided=datetime.now().isoformat(timespec="seconds"))
        log(f"[수정안] {status} (id={rid}, 답장={reply_text[:20]})", "ok")
        return True
    return False
