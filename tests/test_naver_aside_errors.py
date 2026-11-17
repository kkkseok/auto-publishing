"""Aside CLI 실패 사유가 빈 문자열로 남지 않는지 — stderr 로만 나오는 연결 오류 (11-17 B 전기밥솥)."""
import subprocess

from publishers import naver_aside


def _fake_run(stdout: bytes, stderr: bytes, rc: int = 1):
    def run(*a, **k):
        return subprocess.CompletedProcess(a, rc, stdout=stdout, stderr=stderr)
    return run


def test_stderr_error_is_reported(monkeypatch):
    monkeypatch.setattr(naver_aside.subprocess, "run", _fake_run(
        b"", b'Error: browser profile for account u2 ("Profile 3") is not connected to the daemon.'))
    r = naver_aside.publish("blog", "t", "<p>b</p>", [], account="u2")
    assert not r.success and "not connected" in r.message


def test_empty_output_still_has_message(monkeypatch):
    monkeypatch.setattr(naver_aside.subprocess, "run", _fake_run(b"", b"", rc=3))
    r = naver_aside.publish("blog", "t", "<p>b</p>", [], account="u2")
    assert not r.success and "rc=3" in r.message
