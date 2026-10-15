"""자동 발행 전 초안 품질 기준."""
from pipelines.blog_job import check_draft


def _draft(**kw):
    d = {"title": "2026 에너지바우처 신청", "intro": "가" * 400, "sources": [{"url": "https://www.bokjiro.go.kr"}],
         "sections": [{"heading": f"h{i}", "paragraphs": ["나" * 400]} for i in range(4)]}
    d.update(kw)
    return d


def test_good_draft_passes():
    assert check_draft(_draft()) == []


def test_blocks_thin_or_unsourced_drafts():
    assert any("출처" in p for p in check_draft(_draft(sources=[])))
    assert any("소제목" in p for p in check_draft(_draft(sections=[{"heading": "h", "paragraphs": ["x"]}])))
    assert any("미확인" in p for p in check_draft(_draft(intro="금액은 [확인필요] " + "가" * 400)))
    assert any("자" in p for p in check_draft(_draft(intro="", sections=[{"heading": f"h{i}", "paragraphs": ["짧다"]} for i in range(4)])))


def test_info_category_from_keyword():
    from common.keyword_planner import info_category
    assert info_category("에너지바우처 신청") == "지원금·복지"
    assert info_category("연말정산 환급금 조회") == "세금·환급"
    assert info_category("국민내일배움카드 신청") == "취업·청년"
    assert info_category("고양이 사료") == ""
