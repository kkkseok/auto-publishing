"""블로그 초안 승인 큐 — 텔레그램 답글로 발행·비공개 발행·폐기·수정 요청.

초안은 몇 시간 뒤에 승인될 수 있어서 파이프라인이 기다리며 붙잡고 있지 않는다(스케줄러
블로킹 → watchdog 재기동 사고 방지). 대신:

    blog_drafts_daily (subprocess)            스케줄러 프로세스 (bridge long-poll)
    ──────────────────────────────            ───────────────────────────────────
    request(): 미리보기 HTML 을                답글 수신 → resolve_by_tg_message_id()
      텔레그램 문서로 보내고 pending 기록  →    상태 갱신 + `python -m pipelines.blog_job`
      (data/blog_drafts.json)                   을 분리 실행해 발행/수정

답글 규칙 (미리보기 문서 메시지에 답글로):
    ok / 승인 / 발행      → 공개 발행
    비공개                → 비공개 발행
    폐기 / no / x          → 버림
    그 밖의 텍스트         → 수정 요청으로 보고 그 지시대로 다시 써서 새 승인 요청
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

import requests

from common.logger import log

_ROOT = Path(__file__).resolve().parent.parent
PATH = _ROOT / "data" / "blog_drafts.json"
_LOCK = threading.Lock()

BLOG_LABEL = {"A": "네이버·생활정보", "B": "네이버·가격", "C": "티스토리·생활정보", "D": "티스토리·가격"}
_APPROVE = {"ok", "o", "y", "yes", "승인", "발행", "ㅇ", "👍"}
_PRIVATE = {"비공개", "private"}
_REJECT = {"no", "n", "x", "폐기", "거절", "거부", "삭제"}


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def get(draft_id: str) -> dict | None:
    return _load().get(draft_id)


def update(draft_id: str, **fields) -> None:
    with _LOCK:
        data = _load()
        if draft_id in data:
            data[draft_id].update(fields, updated=datetime.now().isoformat(timespec="seconds"))
            _save(data)


def _tg(method: str, **kwargs) -> dict | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not os.getenv("TELEGRAM_CHAT_ID", "").strip():
        log("[초안승인] TELEGRAM_BOT_TOKEN/CHAT_ID 미설정", "error")
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=30, **kwargs)
        if not r.ok:
            log(f"[초안승인] {method} {r.status_code}: {r.text[:200]}", "error")
            return None
        return r.json().get("result")
    except requests.RequestException as e:
        log(f"[초안승인] {method} 예외: {e}", "error")
        return None


def notify(text: str) -> None:
    _tg("sendMessage", json={"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": text[:4096],
                             "disable_web_page_preview": True})


def request(blog: str, keyword: str, draft: dict, draft_dir: Path, preview: Path,
            meta: str = "", draft_id: str | None = None) -> str | None:
    """미리보기 HTML 을 텔레그램 문서로 보내고 승인 대기로 기록. draft_id 반환."""
    draft_id = draft_id or uuid.uuid4().hex[:10]
    points = "\n".join(f"• {p.replace('**', '')}" for p in draft.get("summary_points", [])[:3])
    caption = (f"📝 [{blog}] {BLOG_LABEL.get(blog, blog)} 초안 승인 요청\n"
               f"{draft['title']}\n"
               f"키워드: {keyword}{'  ' + meta if meta else ''}\n\n{points}\n\n"
               f"이 메시지에 답글: ok=공개 발행 · 비공개 · 폐기 · 그 외=수정 요청")
    with open(preview, "rb") as f:
        res = _tg("sendDocument",
                  data={"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "caption": caption[:1024],
                        "reply_markup": json.dumps({"force_reply": True,
                                                    "input_field_placeholder": "ok / 비공개 / 폐기 / 수정 요청"})},
                  files={"document": (f"{blog}_{keyword[:20]}.html", f, "text/html")})
    if not res:
        return None
    with _LOCK:
        data = _load()
        data[draft_id] = {"blog": blog, "keyword": keyword, "title": draft["title"],
                          "dir": str(draft_dir), "status": "pending", "tg_message_id": res.get("message_id"),
                          "created": datetime.now().isoformat(timespec="seconds")}
        _save(data)
    log(f"[초안승인] 요청 {draft_id} [{blog}] {draft['title']}", "ok")
    return draft_id


def _decide(text: str) -> str:
    t = text.strip().lower()
    if t in _APPROVE:
        return "publish"
    if t in _PRIVATE:
        return "publish_private"
    if t in _REJECT:
        return "reject"
    return "revise"


def _spawn(*args: str) -> None:
    """발행/수정 작업을 분리 실행 — 텔레그램 수신 스레드를 붙잡지 않는다."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    log_path = _ROOT / "logs" / "blog_job.log"
    log_path.parent.mkdir(exist_ok=True)
    with open(log_path, "ab") as lf:
        subprocess.Popen([sys.executable, "-u", "-m", "pipelines.blog_job", *args], cwd=_ROOT,
                         stdout=lf, stderr=lf, creationflags=flags,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def resolve_by_tg_message_id(msg_id: int, reply_text: str) -> bool:
    """bridge long-poll 에서 호출. 이 큐의 메시지에 대한 답글이면 처리하고 True."""
    with _LOCK:
        data = _load()
        hit = next((k for k, v in data.items() if v.get("tg_message_id") == msg_id), None)
        if not hit:
            return False
        item = data[hit]
        if item.get("status") != "pending":
            notify(f"이미 처리된 초안입니다 ({item.get('status')}): {item['title']}")
            return True
        action = _decide(reply_text)
        item["status"] = {"publish": "publishing", "publish_private": "publishing",
                          "reject": "rejected", "revise": "revising"}[action]
        item["reply"] = reply_text[:500]
        _save(data)
    if action == "reject":
        notify(f"🗑 폐기: {item['title']}")
    elif action == "revise":
        notify(f"✏️ 수정 요청 접수 — 다시 써서 보내드릴게요: {item['title']}")
        _spawn("revise", hit)
    else:
        private = action == "publish_private"
        notify(f"🚀 {'비공개 ' if private else ''}발행 시작: {item['title']}")
        _spawn("publish", hit, *(["--private"] if private else []))
    log(f"[초안승인] {hit} → {action}", "ok")
    return True
