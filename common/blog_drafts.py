"""블로그 초안 기록 — data/blog_drafts.json.

초안은 승인 없이 바로 발행한다 (2026-10-16 사용자 결정). 네이버는 만들자마자 발행하고,
티스토리는 캡차를 사람이 답해야 해서 status=ready 로 두었다가 pipelines.tistory_publish
일정에 발행한다. 사람이 보지 않으므로 발행 전 품질 기준(pipelines.blog_job.check_draft)을
통과한 초안만 내보내고, 결과(발행 주소·실패 사유)는 여기에 남기고 텔레그램으로 알린다.
"""
from __future__ import annotations

import json
import os
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


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)


def register(blog: str, keyword: str, draft: dict, draft_dir: Path, status: str = "drafted") -> str:
    draft_id = uuid.uuid4().hex[:10]
    with _LOCK:
        data = _load()
        data[draft_id] = {"blog": blog, "keyword": keyword, "title": draft["title"],
                          "dir": str(draft_dir), "status": status,
                          "created": datetime.now().isoformat(timespec="seconds")}
        _save(data)
    return draft_id


def ready(blogs: tuple[str, ...]) -> list[str]:
    """발행 대기(status=ready) 초안 id — 오래된 것부터."""
    data = _load()
    ids = [k for k, v in data.items() if v.get("status") == "ready" and v.get("blog") in blogs]
    return sorted(ids, key=lambda k: data[k].get("created", ""))


def get(draft_id: str) -> dict | None:
    return _load().get(draft_id)


def update(draft_id: str, **fields) -> None:
    with _LOCK:
        data = _load()
        if draft_id in data:
            data[draft_id].update(fields, updated=datetime.now().isoformat(timespec="seconds"))
            _save(data)


def notify(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("[블로그] TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 알림 생략", "warn")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          json={"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True})
        if not r.ok:
            log(f"[블로그] 텔레그램 {r.status_code}: {r.text[:200]}", "warn")
    except requests.RequestException as e:
        log(f"[블로그] 텔레그램 예외: {e}", "warn")
