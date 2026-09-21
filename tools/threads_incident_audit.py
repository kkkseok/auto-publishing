"""Write a secret-free, local-only evidence snapshot; never calls external APIs.

python -m tools.threads_incident_audit --output <json-path>
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
MODULES = {"pipelines.coupang_to_threads", "pipelines.newspick_to_threads",
           "pipelines.aliexpress_to_threads", "pipelines.backlink_to_sns",
           "pipelines.newspick_to_sns"}
FILES = ["common/threads_access.py", "common/threads_guard.py",
         "common/threads_approval.py", "common/threads_token.py",
         "publishers/threads.py", "pipelines/tistory_bridge.py",
         "tools/fix_threads_urls.py", "tools/threads_oauth_poll.py",
         "tests/test_threads_safety.py"]


def snapshot() -> dict:
    env = dotenv_values(ROOT / ".env")
    rows = json.loads((ROOT / "data/pipeline_runs.json").read_text(encoding="utf-8"))
    queue = json.loads((ROOT / "data/publish_queue.json").read_text(encoding="utf-8"))
    posts = [r for r in queue if r.get("platform") == "threads"]
    old_path = ROOT / "docs/meta-appeal-2026-09/threads_gate_activity_log_2026-08-25_to_2026-09-14.csv"
    with old_path.open(encoding="utf-8-sig", newline="") as stream:
        old_events = collections.Counter(r["event"] for r in csv.DictReader(stream))
    events = []
    for row in rows:
        if row.get("module") not in MODULES:
            continue
        tail = row.get("stderr_tail", "")
        events.append({"started_at_kst": row["started_at"], "module": row["module"],
                       "pipeline_status": row["status"],
                       "quality_blocks_in_retained_tail": tail.count("Threads 발행 차단 (게이트)"),
                       "approval_blocks_in_retained_tail": tail.count("Threads 발행 보류 (승인)"),
                       "app_deactivation_mentions_in_retained_tail": tail.lower().count("api access deactivated")})
    schedules = ["SCHEDULE_COUPANG_THREADS", "SCHEDULE_NEWSPICK_THREADS",
                 "SCHEDULE_ALIEXPRESS_THREADS", "SCHEDULE_THREADS_REFRESH", "SCHEDULE_BACKLINK_SNS"]
    keys = schedules + ["THREADS_GUARD_ENABLED", "THREADS_APPROVAL_REQUIRED",
                        "THREADS_MAX_PER_DAY", "THREADS_MAX_AFFILIATE_PER_DAY",
                        "THREADS_MAX_AFFILIATE_RATIO", "THREADS_MIN_CHARS",
                        "THREADS_REQUIRE_KOREAN", "THREADS_MIN_KOREAN_RATIO"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "restricted_app_id_from_user_screenshot": "1655751472297844",
        "scope": "Local repository and retained logs only; no Meta dashboard or API verification",
        "limitations": ["stderr_tail is partial, not a complete HTTP traffic log",
                        "pipeline runs, gate events and posts are different units",
                        "queue records do not prove account-wide posting history",
                        "local control thresholds are not Meta policy thresholds"],
        "configuration_allowlist": {k: env.get(k, "") for k in keys},
        "api_hold": json.loads((ROOT / "data/threads_access.json").read_text(encoding="utf-8")),
        "scheduler": json.loads((ROOT / ".runtime/scheduler_heartbeat").read_text(encoding="utf-8")),
        "previous_csv_event_counts": dict(old_events),
        "retained_ledger_span": [min(r["started_at"] for r in rows), max(r["started_at"] for r in rows)],
        "retained_threads_related_runs": events,
        "local_threads_queue_count": len(posts),
        "latest_local_threads_queue_timestamp": max((r.get("queued_at", "") for r in posts), default=""),
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in FILES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Local evidence written: {args.output}")


if __name__ == "__main__":
    main()
