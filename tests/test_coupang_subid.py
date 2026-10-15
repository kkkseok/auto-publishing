"""쿠팡 subId 규칙 — 영숫자만, 글마다 고유."""
import re
from datetime import datetime, timedelta, timezone

from sources.coupang import _tagged_subid, post_subid


def test_tagged_subid_strips_separators():
    # 하이픈·점은 리포트에서 잘리거나 합쳐지는 것으로 보여 제거한다.
    assert _tagged_subid("kkkseok4989", "search") == "kkkseok4989"
    assert _tagged_subid("sasohan.insight", "goldbox") == "sasohaninsight"
    assert _tagged_subid("", "search") == ""


def test_post_subid_is_alphanumeric_and_unique(monkeypatch):
    monkeypatch.setenv("COUPANG_SUBID_PREFIX", "ap")
    t = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    a = post_subid("ts", now=t)
    b = post_subid("ts", now=t + timedelta(seconds=1))
    assert re.fullmatch(r"[a-z0-9]+", a)
    assert a.startswith("apts")
    assert a != b
    assert len(a) <= 12


def test_post_subid_sanitizes_input(monkeypatch):
    monkeypatch.setenv("COUPANG_SUBID_PREFIX", "a-p")
    s = post_subid("N.B!", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert s.startswith("apnb")
    assert re.fullmatch(r"[a-z0-9]+", s)
