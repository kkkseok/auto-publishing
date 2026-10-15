"""초안 승인 답글 해석."""
from common.blog_drafts import _decide


def test_decide():
    assert _decide("ok") == "publish"
    assert _decide(" 승인 ") == "publish"
    assert _decide("비공개") == "publish_private"
    assert _decide("폐기") == "reject"
    assert _decide("X") == "reject"
    assert _decide("제목을 더 짧게 바꿔줘") == "revise"
