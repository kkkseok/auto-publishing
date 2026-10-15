"""
일일 백업 파이프라인.

백업 대상: data/ 의 운영 데이터 (publish_queue, keyword_pool, coupang_roi,
pipeline_runs, tistory_queue 등).

백업하지 않는 것 (2026-10-15 변경):
  - .env, .sessions/, data 안의 쿠키·세션 파일 — 예전엔 이것들까지 평문 zip 에
    담아 하루 ~510MB, 14일 ~7GB 의 시크릿 사본이 디스크에 쌓였다. 세션은
    만료되면 어차피 재로그인해야 해서 복원 가치도 낮다. .env 는 사용자가
    비밀번호 관리자 등에 따로 보관한다.
  - .runtime/ — 하트비트·임시 파일뿐이고, 수동 .env 백업이 놓이는 곳이라 제외.

저장 위치: backups/YYYY-MM-DD.zip  (BACKUP_DIR env 로 변경 가능)
보관 정책: 최근 BACKUP_KEEP_DAYS (기본 14) 일치만 유지, 그 외 자동 삭제

스케줄: SCHEDULE_BACKUP=03:30  (.env)

수동 실행:
    python -m pipelines.backup

검증:
    python -m pipelines.backup --list
"""
import os
import re
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.logger import log


_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_BACKUP_DIR = _BASE_DIR / "backups"


SCHEDULE = {
    "env":  "SCHEDULE_BACKUP",
    "func": "run",
}


def _backup_dir() -> Path:
    raw = os.getenv("BACKUP_DIR", "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_BACKUP_DIR


def _keep_days() -> int:
    try:
        return max(1, int(os.getenv("BACKUP_KEEP_DAYS", "14")))
    except ValueError:
        return 14


# data/ 안에서도 로그인 쿠키·세션을 담는 파일은 제외한다.
_SECRET_NAME_PARTS = ("storage", "session", "cookie", "token")
_SECRET_SUFFIXES = (".pkl",)


def _is_secret(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix in _SECRET_SUFFIXES or any(k in name for k in _SECRET_NAME_PARTS)


def _iter_files(targets: list[Path]):
    """백업 대상의 파일을 모두 순회 (rel_path, abs_path)."""
    for t in targets:
        if not t.exists():
            continue
        if t.is_file():
            yield t.name, t
            continue
        for p in t.rglob("*"):
            if p.is_file():
                # 캐시/로그 류 제외 — sqlite WAL 등은 일관성 위해 포함
                if p.suffix in (".pyc", ".tmp") or "__pycache__" in p.parts:
                    continue
                if _is_secret(p):
                    continue
                rel = p.relative_to(_BASE_DIR)
                yield str(rel).replace("\\", "/"), p


def _create_backup() -> Path:
    """오늘자 zip 생성. 같은 날짜 중복 호출 시 덮어씀."""
    out_dir = _backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    out_path = out_dir / f"{today}.zip"

    targets = [_BASE_DIR / "data"]

    n_files = 0
    total_bytes = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED,
                          compresslevel=6) as zf:
        for rel, abs_path in _iter_files(targets):
            try:
                zf.write(abs_path, rel)
                n_files += 1
                total_bytes += abs_path.stat().st_size
            except (OSError, PermissionError) as e:
                # 락 걸린 sqlite/현재 쓰기 중 파일은 스킵 — 백업이 멈추지 않게
                log(f"  스킵 ({rel}): {e}", "warn")

    size_mb = out_path.stat().st_size / 1024 / 1024
    log(f"[Backup] {out_path.name} 생성 — {n_files}개 파일 / "
        f"원본 {total_bytes/1024/1024:.1f}MB → 압축 {size_mb:.1f}MB", "ok")
    return out_path


_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.zip$")


def _rotate(keep_days: int) -> int:
    """오래된 zip 삭제. 삭제된 개수 반환."""
    out_dir = _backup_dir()
    if not out_dir.exists():
        return 0

    today = date.today()
    deleted = 0
    for p in out_dir.iterdir():
        m = _DATE_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (today - d).days
        if age > keep_days:
            try:
                p.unlink()
                deleted += 1
                log(f"  rotate 삭제: {p.name} (age={age}d)", "info")
            except OSError as e:
                log(f"  rotate 실패 {p.name}: {e}", "warn")
    return deleted


def run() -> None:
    """일일 백업 + rotation. 텔레그램 알림 동봉."""
    log("[Backup] 시작", "step")

    try:
        out_path = _create_backup()
    except Exception as e:
        log(f"[Backup] 실패: {e}", "error")
        try:
            from common.notifier import _send_telegram
            _send_telegram(f"🚨 [Backup] 실패: {e}")
        except Exception:
            pass
        return

    deleted = _rotate(_keep_days())

    size_mb = out_path.stat().st_size / 1024 / 1024
    msg = (
        f"💾 [Backup] {out_path.name} ({size_mb:.1f}MB)\n"
        f"• 보관: 최근 {_keep_days()}일 / 정리됨: {deleted}개"
    )
    log(msg, "ok")
    try:
        from common.notifier import _send_telegram
        _send_telegram(msg)
    except Exception:
        pass


def list_backups() -> list[dict]:
    """저장된 zip 목록 (디버그용)."""
    out_dir = _backup_dir()
    if not out_dir.exists():
        return []
    rows = []
    for p in sorted(out_dir.iterdir()):
        if not _DATE_RE.match(p.name):
            continue
        rows.append({
            "name":     p.name,
            "size_mb":  round(p.stat().st_size / 1024 / 1024, 2),
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(
                timespec="seconds"),
        })
    return rows


if __name__ == "__main__":
    if "--list" in sys.argv:
        rows = list_backups()
        print(f"백업 위치: {_backup_dir()}")
        print(f"보관 정책: 최근 {_keep_days()}일\n")
        if not rows:
            print("(없음)")
        else:
            print(f"{'파일':<20} {'크기(MB)':>10}  생성")
            print("-" * 60)
            for r in rows:
                print(f"{r['name']:<20} {r['size_mb']:>10}  {r['modified']}")
    else:
        run()
