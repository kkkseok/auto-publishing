"""Persistent Threads API hold shared by publishing and token maintenance.

Missing/corrupt state blocks access. After Meta confirms reinstatement, run:
python -m common.threads_access resume --reference <case-or-notice-reference>
This records an operator confirmation; it does not verify Meta's decision.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_PATH = Path(__file__).resolve().parent.parent / "data" / "threads_access.json"
_session_hold = ""


def blocked_reason() -> str:
    if _session_hold:
        return _session_hold
    try:
        state = json.loads(_PATH.read_text(encoding="utf-8"))
        if state.get("status") == "active" and state.get("restoration_reference"):
            return ""
        return "Threads API 중지: " + str(state.get("reason", "Meta 복구 확인 필요"))
    except (OSError, ValueError, AttributeError):
        return "Threads API 중지: 복구 확인 기록 없음 또는 상태 파일 손상"


def _write(state: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_name(f"{_PATH.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PATH)
    finally:
        tmp.unlink(missing_ok=True)


def suspend(reason: str) -> None:
    global _session_hold
    _session_hold = "Threads API 중지: " + reason
    _write({"status": "suspended", "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat()})


def observe_error(body: str) -> None:
    """Persist explicit app deactivation without storing response bodies/secrets."""
    if "api access deactivated" in (body or "").lower():
        suspend("Meta returned API access deactivated; reinstatement required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "suspend", "resume"))
    parser.add_argument("--reference", default="")
    args = parser.parse_args()
    if args.action == "suspend":
        suspend("Operator hold pending Meta review")
    elif args.action == "resume":
        if not args.reference.strip():
            parser.error("--reference must identify the Meta reinstatement notice")
        _write({"status": "active", "restoration_reference": args.reference.strip(),
                "updated_at": datetime.now(timezone.utc).isoformat()})
    print(blocked_reason() or "Threads API access enabled by operator confirmation")


if __name__ == "__main__":
    main()
