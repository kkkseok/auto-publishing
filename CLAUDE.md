# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Korean multi-platform content publishing automation. Pipelines pull from
content sources (Coupang products via the Partners API, public data),
generate posts via Claude/Gemini, and publish to WordPress, Tistory, Naver,
GitHub Pages, Twitter, Threads and Pinterest. A `schedule`-
based runner discovers pipelines via a `SCHEDULE` dict declared in each module
and runs them per `.env` cron-style times.

Read `README.md` for the user-facing setup walkthrough. This file is for
internal navigation only.

## Common Commands

```bash
# Project venv (Python 3.13) — pinned deps; scheduled tasks use it once re-registered
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\activate

# Unit tests (pure logic only — they don't hit real services)
python -m pytest -q

# Run a single pipeline (replaces module path)
python -u -m pipelines.coupang_to_tistory
python -u -m pipelines.coupang_to_threads

# Run the scheduler (long-lived; usually started by Task Scheduler instead)
python -m pipelines.scheduler_runner

# Bridge HTTP server alone (debugging — production is embedded in scheduler)
python -m pipelines.tistory_bridge

# Quick state probes
python -m tools.reset_tistory_queue                  # unstick claimed/failed queue items
python -m tools.fix_threads_urls --dry-run           # diagnose then re-run without --dry-run

# One-off Bash health checks
Invoke-RestMethod http://localhost:5757/healthz
Invoke-RestMethod http://localhost:5757/captcha/state -Headers @{"X-Bridge-Client"="auto-publishing-ext"}

# Manual session refresh
python -m scripts.tistory_manual_login

# Windows scheduled task management (admin PowerShell)
powershell -ExecutionPolicy Bypass -File tools\install_task_scheduler.ps1
powershell -ExecutionPolicy Bypass -File tools\install_chrome_autostart.ps1
Stop-ScheduledTask -TaskName AutoPublishing_Scheduler
Start-ScheduledTask -TaskName AutoPublishing_Scheduler
```

`tests/` holds pytest unit tests for pure logic (parsing, guards, backup,
session health). There is no linter or build step. Anything that touches a
real service is verified by running the pipeline with diagnostic logging, then
checking `data/pipeline_runs.json` and `data/publish_queue.json`.

`requirements.txt` pins the runtime versions the scheduler actually runs;
`requirements-dev.txt` adds pytest and the Streamlit dashboard.
`tools/install_task_scheduler.ps1` registers `.venv\Scripts\python.exe` when
`.venv` exists and falls back to the first `python` on PATH otherwise.

Windows console emits `cp949` and crashes on `—` / emoji output. Either
prepend `python -u` (and ensure scheduler wrapper sets `PYTHONIOENCODING=utf-8`),
or in Python scripts call `sys.stdout.reconfigure(encoding='utf-8')` early —
several `tools/*.py` already do this.

## Architecture

### Pipeline structure

Each pipeline is `pipelines/<source>_to_<target>.py` with this contract:

```python
SCHEDULE = {
    "env":  "SCHEDULE_FOO_BAR",         # cron times come from .env
    "func": "run",
    "args_from_env": ("CATEGORY:default", "POST_COUNT:1:int"),
}

def run(...) -> None:
    ...
```

`pipelines/scheduler_runner.py` discovers any module under `pipelines/` whose
`SCHEDULE` dict is defined, then schedules `_safe_subprocess_call(module_name)`
at every comma-separated time in the env var. Each slot runs as a fresh
`python -m <module>` subprocess (isolates Playwright / Chromium state),
captures stderr, and writes a record to `data/pipeline_runs.json` via
`common/run_ledger.py`.

Status detection: `proc.returncode == 0` alone is not enough — many pipelines
log `[ERROR]` but exit 0 (e.g. partial publish failure). The scheduler scans
stderr for `[ERROR]` lines and downgrades status to `failure` when no
`발행 성공/완료` marker is found. `common/run_diagnosis.py` then turns the
stderr tail into a one-line cause label for the daily summary.

Common kernels share publish loops:

- `pipelines/_kernel/product_wp.py` — Coupang → WordPress
- `pipelines/_kernel/base_runner.py` — generic fetch → publish → notify loop
- `pipelines/_riseset_common.py` — sunrise/sunset post body builder

The kernels accept a config object (`ProductWpConfig`) that
plugs in the source factory, publisher factory, and theme.

### Sources, publishers, common

- `sources/` modules pull data and return dicts. Keyword sources expose
  `get_next_keywords(n, categories=...)` against `data/keyword_pool.json`,
  with `mark_keywords_used()` to enforce no-duplication via
  `data/used_keywords.json`.
- `publishers/` implement `Publisher` (`publishers/base.py`) with `login()`
  and `post(title, content, tags, category, image_url, **kwargs)`. Tistory
  has two: `publishers/tistory.py` (web, persistent Chromium profile) and
  `publishers/tistory_bridge.py` (queue-only; see Bridge section).
- `common/` holds cross-cutting helpers — `notifier.py` (Telegram + Kakao,
  both fire-and-forget, neither blocks the pipeline), `kakao_token.py`
  (auto-refresh access_token using refresh_token from `.env`),
  `threads_token.py`, `tistory_blogs.py` (`make_publisher(blog_name)`
  dispatches to web or bridge publisher based on `TISTORY_PUBLISHER`),
  `tistory_queue.py` (bridge queue + captcha answer in-memory store),
  `ai_intro.py` (Claude CLI → Gemini fallback for intro/title/tags).

### Tistory DKAPTCHA bypass (Bridge + Extension)

The single biggest piece of architecture that requires reading multiple files
to understand. Tistory introduced server-side DKAPTCHA (Daum's Korean visual
captcha) on `/manage/post.json` around 2026-05-16, and the OAuth API was
retired in early 2024. There is no automated publish path. The workaround
runs the post-fill + publish-click step inside the user's regular Chrome via
a Manifest V3 extension, with the captcha relayed to the user's phone over
Telegram.

```
python pipeline (TistoryBridgePublisher.post)
  → common.tistory_queue.enqueue() → data/tistory_queue.json
                                              ▲
                                              │ GET /next (claim_next)
                                              │
pipelines.tistory_bridge HTTP server (port 5757, embedded as daemon thread
  in pipelines.scheduler_runner when TISTORY_PUBLISHER=bridge)
  ├── /next            extension polling endpoint
  ├── /captcha/needed  receives screenshot from extension → Telegram sendPhoto
  │                    + force_reply, stores tg_message_id in CAPTCHA_PENDING
  ├── /captcha/answer/<id>  extension polls; pops from CAPTCHA_ANSWERS
  ├── /done            marks queue done, records to publish_queue.json,
  │                    fires "Tistory 발행 완료" Telegram (only here, NOT
  │                    from the pipeline — that would be a false positive)
  └── /fail            marks queue failed
  Background thread: Telegram getUpdates long-poll; matches each reply's
  reply_to_message_id against CAPTCHA_PENDING and writes to CAPTCHA_ANSWERS

extension/  (user's regular Chrome, Manifest V3)
  ├── background.js       service worker; chrome.alarms polls /next every 30s
  │                       (Chrome 120+ minimum), opens new tab for claimed
  │                       items, chrome.tabs.captureVisibleTab for captcha,
  │                       chrome.webNavigation detects publish success
  ├── content.js          isolated world; fills title (textarea#post-title-inp),
  │                       tags (#tagText), clicks #publish-layer-btn → visibility
  │                       radio → #publish-btn; detects DKAPTCHA modal; relays
  │                       captcha to background; polls /captcha/answer
  ├── main_world.js       runs in "world": "MAIN" so it can touch window.tinymce
  │                       (isolated world cannot see page globals). Exposes
  │                       wait-tinymce / set-content RPC via window.postMessage.
  ├── captcha_frame.js    injected via "matches": ["https://dkaptcha.kakao.com/*"]
  │                       with all_frames:true because DKAPTCHA renders in a
  │                       cross-origin iframe (dkaptcha.kakao.com) and the parent
  │                       page cannot reach into its DOM. Receives fill-captcha
  │                       from background via chrome.tabs.sendMessage with
  │                       frameId, types the answer, clicks 답변 제출.
  └── popup.html/js       enable toggle + bridge healthz display + queue stats
```

Key gotchas in this flow (each was an actual debugging session):

- **Bridge access control**: the bridge binds 127.0.0.1 but any web page open
  in the same Chrome can reach localhost. `BridgeHandler._reject_foreign`
  rejects a non-localhost `Host` (DNS rebinding), any `Origin` that is not
  `chrome-extension://` (optionally pinned by `TISTORY_BRIDGE_EXTENSION_ID`),
  and every path except `/healthz` that lacks `X-Bridge-Client:
  auto-publishing-ext`. Web pages cannot attach that header without a
  preflight, which the Origin check rejects. Consequence: content scripts
  must not call the bridge directly (they carry the page's origin) — they go
  through `chrome.runtime.sendMessage` (`captcha-answer`) to background.js,
  whose `bridgeFetch` adds the header. Manual probes need the header too.
- **content_script worlds**: `window.tinymce` is on the page's window, not the
  isolated-world content script's. Anything that touches page globals goes
  through `main_world.js` via `window.postMessage` RPC.
- **Cross-origin captcha iframe**: parent page cannot read `iframe.contentDocument`
  for `dkaptcha.kakao.com`. The fix is a *separate* content script registered
  for that origin with `all_frames: true`, talked to from background via
  `chrome.tabs.sendMessage(..., { frameId })`.
- **DKAPTCHA submit button disabled-state**: pasting `input.value` does not flip
  the framework's dirty/touched state and the "답변 제출" button stays
  disabled. `captcha_frame.js` simulates per-character `keydown`/`InputEvent`/
  `keyup` with `keyCode` patched in, then types → backspaces all → re-types
  to flip the touched flag.
- **`navigator.tabs.captureVisibleTab` permission**: requires `<all_urls>`
  host_permission. Specific tistory.com host permission is not enough.
- **Captcha URL after submit**: Tistory redirects to `/manage/posts`, not the
  new post's URL. `background.onTabNavigated` falls back to GET
  `https://<blog>.tistory.com/manage/posts.json` to recover the canonical
  post URL when the navigation lands on the list page.
- **TISTORY_BRIDGE_WAIT_SEC**: default 0 means the pipeline is fire-and-forget.
  It returns "queued" immediately; the Telegram "발행 완료" message comes
  from the bridge's `/done` handler, NOT from the pipeline. Pipelines skip
  their own `notify_pipeline_result` call in bridge mode for this reason.

### Threads publishing — permalink fetch

`publishers/threads.py` was constructing `https://www.threads.net/t/<numeric_id>`
from `threads_publish`'s id field, but that path expects an alphanumeric
shortcode and 404s on numeric IDs. `_publish_container` now calls
`GET /v1.0/{id}?fields=permalink` immediately after publish to retrieve the
canonical `@<username>/post/<shortcode>` URL. `tools/fix_threads_urls.py`
back-fills broken URLs already in `publish_queue.json`.

### Affiliate disclosure placement (`common/affiliate_notice.py`)

Coupang Partners rejected final approval on 2026-08-10 with: "대가성 문구는
활동 게시물 최상단 혹은 제목에 기재하셔야 합니다" (target post was a Threads
one). The old code put the disclosure in a *footer* and used the conditional
phrasing "수수료를 제공받을 수 있습니다" — both are explicit violations.

`common/affiliate_notice.py` is now the only place a disclosure string exists.
Three requirements it encodes, all of which the reviewer checks:

1. First element of the post body (or the title) — never the footer, never
   after tags/links/images.
2. Visually distinct from body copy — 16px bold `#e4000f` on a bordered box
   (body is 14–15px grey).
3. Declarative wording only. `제공받습니다`, never `제공받을 수 있습니다`.

Every channel goes through it:

| Channel | Insertion point |
|---------|-----------------|
| WP / Tistory / GitHub (product posts) | `render_product_post()` — first node inside the `wp:html` wrapper |
| Tistory/WP/Naver (card-appended posts) | pipeline calls `prepend_html()` before `post()` |
| Naver Blog | `_notice_component()` right after `documentTitle` in all 4 SE builders |
| Naver Cafe | `_notice_component()` as `components[0]` in all 3 SE builders + first line of the affiliate comment |
| Threads | `prepend_text()` on chain part 0 **and** the part carrying the link (replies surface as standalone posts) |
| Pinterest | first line of the pin description |

`ensure_html()` is a last-resort net wired into `tistory.py`,
`tistory_bridge.py`, `wordpress.py`, `github_pages.py` `post()`: if the body
contains a coupang/aliexpress link but no notice, it prepends one. It cannot
see through shortened URLs, so pipelines must still insert explicitly — the net
only catches a regression, it is not the mechanism.

All helpers are idempotent (`has_notice()` matches `<!--affiliate-notice-->`
or the visible `[광고]` label), so double insertion is impossible.
`AFFILIATE_NOTICE_IN_TITLE=true` additionally prefixes titles with `[광고]` —
off by default because it costs CTR; turn it on only if review fails again.

### Traffic feedback loop (2026-10-19)

Publishing alone produced 840 posts nobody read, so every published post's
traffic now feeds back into what gets written next:

```
pipelines/traffic_collect (08:20)  → data/traffic.db
  sources/traffic_gsc     Search Console API (ADC, webmasters.readonly) — C·D per page+query
  sources/traffic_naver   blog.stat.naver.com internal JSON via the blog's Aside session — A·B
                          per-post views + blog-level search queries (match posts by logNo:
                          the stat API reports the account's alias id, e.g. bluesky0211)
  → common/content_insights.build() → data/content_insights.json
       keyword_planner.plan()  × feedback(): topic multiplier 0.5–2.0 + 1.3 for queries seen
       draft_writer.write()    "운영 데이터" prompt block: titles that got visits, real queries
       weekly_kpi              유입 section
pipelines/content_revise (08:50)  low_ctr / striking posts → rewrite via write(feedback, previous)
  → check_draft → <blog>_draft_rev<n>.json → Telegram force_reply → bridge long-poll
  → common/revisions resolve_by_tg_message_id (approved/rejected in data/revisions.json)
```

Posts younger than 14 days are `new` and never judged; with no mature posts
every multiplier is 1.0, i.e. the old behaviour. Applying an approved revision
to the live post is **not implemented yet** — approvals only get recorded.

### Removed tracks (2026-10-15)

Newspick and AliExpress pipelines, sources, login tools and the
`tistory_session_keepalive` pipeline were deleted during the re-planning:
Newspick re-published other sites' articles, and AliExpress needed a manual
Google login every 1–2 weeks plus a slider captcha. Unscheduled image/cafe/
Instagram pipelines and the DKAPTCHA probe tools went with them. All of it is
in git history before commit "Remove Newspick, AliExpress and unused pipelines".
`publishers/tistory.py` (web mode, dead under DKAPTCHA) is still imported by
the Tistory pipelines' `is_bridge` branches and goes when those are rebuilt.

### Windows operations

Three scheduled tasks:

- `AutoPublishing_Scheduler` — runs `python -m pipelines.scheduler_runner` at
  startup with 1-minute delay; restarts up to 3 times on failure;
  `MultipleInstances IgnoreNew`. Registered by `tools/install_task_scheduler.ps1`.
  Bridge HTTP server is embedded as a daemon thread when
  `TISTORY_PUBLISHER=bridge`.
- `AutoPublishing_Watchdog` — runs `tools/watchdog.py` every 5 minutes;
  reads `.runtime/scheduler_heartbeat`; if stale >5 min, sends a Telegram
  alert and triggers `Stop-ScheduledTask` + `Start-ScheduledTask` to revive.
  All `RunLevel=Highest`, so `taskkill` from a non-elevated PowerShell will
  hit `Access denied` and the singleton guard's failure-to-kill list is
  what notifies that case.
  Registered with `pythonw.exe` (not `python.exe`) so the 5-minute cadence
  doesn't flash a console window. pythonw leaves `sys.stdout`/`sys.stderr`
  as `None`, so `common.logger`'s console output silently vanishes —
  `tools/watchdog.py` therefore owns a `RotatingFileHandler` writing
  `logs/watchdog.log`, and its `wlog()` (not `log()`) is what every code
  path must use. The `Stop`/`Start-ScheduledTask` subprocess runs with
  `CREATE_NO_WINDOW` for the same reason.
- `AutoPublishing_Chrome` — runs `tools/chrome_background_launcher.ps1` at
  logon. The wrapper uses `WScript.Shell.Run` with `SW_SHOWMINNOACTIVE` (=7)
  so Chrome starts minimized without focus, and reads
  `%LOCALAPPDATA%\Google\Chrome\User Data\Local State` to auto-pick the
  user's `profile.last_used` (the one with the extension installed) —
  Chrome 116+ tends to ignore `--no-startup-window` and `--start-minimized`
  silently. Without this, the extension does not run when no Chrome window
  is open.

Orphan Playwright Chromium cleanup is built into
`common/browser_profile.py` `_kill_orphan_chromium_windows()`: when a parent
Python is force-killed, child Chromiums survive and lock
`.sessions/<name>_profile/`, so persistent_context launch fails with
"Target page, context or browser has been closed". The cleanup matches
`ExecutablePath like *ms-playwright*` to avoid touching the user's regular
Chrome at `Program Files\Google\Chrome`.

## Notable Files

| Path | Why it matters |
|------|----------------|
| `pipelines/scheduler_runner.py` | Single entrypoint for all scheduled work. Modify `_safe_subprocess_call` to change ledger / status / notification policy. |
| `pipelines/tistory_bridge.py` | Bridge HTTP server. Endpoints + Telegram long-poll thread both live here. `start_server_in_thread()` is what scheduler_runner imports for the embedded mode. |
| `common/tistory_queue.py` | Queue file format + in-memory captcha state. Bridge and `TistoryBridgePublisher` both import from here. |
| `common/tistory_blogs.py` | `make_publisher(blog_name)` returns web or bridge publisher based on `TISTORY_PUBLISHER`. All `*_to_tistory.py` pipelines must use this, not `TistoryPublisher` directly. |
| `extension/manifest.json` | Three content_script entries — one in MAIN world for `*.tistory.com/manage/newpost*`, one isolated for the same, one all_frames for `dkaptcha.kakao.com/*`. Host permissions include `<all_urls>` because `tabs.captureVisibleTab` requires it. |
| `publishers/threads.py` | `_publish_container` must call `_fetch_permalink` to get the canonical URL. Reverting that change re-introduces 404 URLs in publish_queue. |
| `publishers/naver_aside.py` | Naver publishing through the Aside browser (`aside repl`), the only path that passes Naver's bot check. Tistory stays on the Chrome extension bridge — an Aside attempt (2026-10-16) stalled after the publish click in Aside's background tab. Captchas are relayed to a human over Telegram, never auto-solved. |
| `pipelines/blog_drafts_daily.py` → `pipelines/blog_job.py` | Daily keyword → research → write → publish with no approval step; `check_draft()` is the quality gate that replaces the human. `BLOG_DAILY_BLOGS` picks the blogs. Naver posts go out immediately; Tistory drafts wait as `ready` until `pipelines/tistory_publish.py` (`SCHEDULE_TISTORY_PUBLISH`) hands one to the extension queue, so the captcha arrives while the user is awake. |
| `common/affiliate_notice.py` | Single source of truth for the paid-partnership disclosure. Moving it back to a footer, or reintroducing "받을 수 있습니다", re-triggers Coupang Partners rejection. |
| `common/notifier.py` | `_send_telegram` is reused by bridge for "발행 완료" messages. Pipeline-side `notify_pipeline_result` is skipped when `TISTORY_PUBLISHER=bridge` so users don't get false-positive "발행 성공" messages while the queue is still being processed. |
| `common/run_diagnosis.py` | Stderr-pattern → cause label mapping for the daily summary. Add patterns here when introducing new failure modes. |
| `tools/probe_*.py`, `tools/test_dkaptcha_*.py`, `tools/capture_editor_publish.py` | Debugging artifacts from the DKAPTCHA investigation. Useful templates for future "what is Tistory doing now" debugging — they demonstrate Playwright + page.evaluate + frame-level inspection patterns. |
