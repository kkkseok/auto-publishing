"""Aside 브라우저 REPL 세션 — 한 프로세스를 열어 두고 단계별로 JS 를 평가한다.

`aside repl` 은 stdin 으로 받은 한 줄을 평가하고 `[ok | 123ms]` 또는 `[error ...]` 로
끝낸다. `var` 변수와 REPL 의 `page` 는 같은 프로세스 안에서 유지되므로, 120초 평가 제한을
넘는 작업(여러 URL 을 차례로 제출하는 일 등)은 여러 줄로 나눠 보낸다.

    with AsideRepl() as r:
        r.run("await openTab('https://example.com')")   # page 는 REPL 이 관리
        out = r.run("console.log('RESULT '+JSON.stringify({t: await page.title()}))")
        data = r.result(out)
"""
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

_ASIDE_DEFAULT = Path(os.getenv("LOCALAPPDATA", "")) / "Aside" / "CLI" / "current" / "aside.exe"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_END = re.compile(r"\[(ok|error)\b[^\]]*\]\s*$", re.I)


class AsideError(RuntimeError):
    pass


def aside_bin() -> str:
    return os.getenv("ASIDE_CLI") or shutil.which("aside") or str(_ASIDE_DEFAULT)


_ASIDE_APP = Path(os.getenv("ASIDE_APP", r"C:\Program Files\Aside\Application\Aside.exe"))
NOT_RUNNING = "Aside isn't running"


def start_aside(wait_sec: int = 15) -> bool:
    """Aside 브라우저가 꺼져 있으면 최소화로 켠다 — CLI 는 실행 중인 브라우저(데몬)에 붙기 때문.

    사용자가 창을 닫으면 06:30 네이버 발행·22:00 색인 제출이 "Aside isn't running" 으로
    실패했다(10-16). 켤 수 없으면 False.
    """
    if not _ASIDE_APP.exists():
        return False
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(_ASIDE_APP)], creationflags=flags, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(wait_sec)
    return True


class AsideRepl:
    def __init__(self, profile: str = "", account: str = ""):
        self.cmd = ([aside_bin(), "repl"] + (["--account", account] if account else [])
                    + (["--profile", profile] if profile else []))
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.session_dir = ""

    def __enter__(self) -> "AsideRepl":
        try:
            return self._open()
        except AsideError as e:
            if NOT_RUNNING not in str(e) or not start_aside():
                raise
            self.lines = queue.Queue()
            return self._open()

    def _open(self) -> "AsideRepl":
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
        except FileNotFoundError:
            raise AsideError(f"aside CLI 없음: {self.cmd[0]}")
        threading.Thread(target=self._pump, daemon=True).start()
        banner = self._read_until(lambda s: "type 'help'" in s, timeout=30)
        m = re.search(r"sessionDir:\s*(.+)", banner)
        self.session_dir = m.group(1).strip() if m else ""
        return self

    def __exit__(self, *exc) -> None:
        if not self.proc:
            return
        try:
            self.proc.stdin.write(b"exit\n")
            self.proc.stdin.flush()
            self.proc.wait(10)
        except Exception:
            self.proc.kill()

    def _pump(self) -> None:
        for raw in iter(self.proc.stdout.readline, b""):
            self.lines.put(_ANSI.sub("", raw.decode("utf-8", "replace")).rstrip())
        self.lines.put(None)

    def _read_until(self, done, timeout: float) -> str:
        buf = []
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                raise AsideError("aside repl 응답 없음:\n" + "\n".join(buf[-10:]))
            if line is None:
                raise AsideError("aside repl 종료됨:\n" + "\n".join(buf[-10:]))
            buf.append(line)
            if done(line):
                return "\n".join(buf)

    def run(self, code: str, timeout: float = 130) -> str:
        """한 줄 평가. 평가가 에러로 끝나면 AsideError.

        REPL 은 최상위 스코프 하나를 계속 쓰므로 최상위 `const`/`let` 은 다음 호출에서
        "already declared" 로 실패한다. 최상위 선언은 `var` 로 쓴다. `sleep(ms)` 는 REPL 내장.
        """
        self.proc.stdin.write((code.replace("\n", " ") + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        out = self._read_until(lambda s: bool(_END.search(s)), timeout)
        if re.search(r"\[error\b", out.splitlines()[-1], re.I):
            raise AsideError(out[-600:])
        return out

    @staticmethod
    def result(out: str) -> dict:
        """`console.log('RESULT '+JSON.stringify(x))` 로 찍은 마지막 값을 읽는다."""
        found = re.findall(r"RESULT (\{.*\})", out)
        if not found:
            raise AsideError("RESULT 없음:\n" + out[-600:])
        return json.loads(found[-1])
