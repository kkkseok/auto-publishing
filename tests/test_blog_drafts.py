"""자동 발행 전 초안 품질 기준."""
from pipelines.blog_job import check_draft


def _draft(**kw):
    d = {"title": "2026 에너지바우처 신청", "intro": "가" * 400, "sources": [{"title": "복지로", "url": "https://www.bokjiro.go.kr"}],
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


def test_price_post_puts_notice_first_and_links_products():
    from common import affiliate_notice
    from common.draft_writer import render_html
    draft = _draft(basis_line="2026.10.16 기준", summary_points=["a"], faq=[], closing="끝", tags=[],
                   sections=[{"heading": "h", "paragraphs": ["p"], "table_columns": [], "table_rows": [],
                              "steps": [], "checklist": [], "callout_kind": "none", "callout_text": ""}])
    prods = [{"name": "에어프라이어 5L", "price": "59,000원", "is_rocket": True, "fetched_at": "2026.10.16 07:00",
              "affiliate_url": "https://link.coupang.com/re/AFFSDP?lptag=X&subid=apts1"}]
    body = affiliate_notice.prepend_html(render_html(draft, {}, "D", prods), affiliate_notice.COUPANG)
    assert body.startswith("<!--affiliate-notice-->")
    assert "subid=apts1" in body and "59,000원" in body and "2026.10.16 07:00 기준" in body


def test_tracked_price_keywords_dedupe_spacing(tmp_path, monkeypatch):
    import json
    import pipelines.price_collector as pc
    used = tmp_path / "used.json"
    used.write_text(json.dumps({"에어프라이어 추천": {"kind": "price"}, "에어프라이어추천": {"kind": "price"},
                                "에너지바우처 신청": {"kind": "info"}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pc, "USED", used)
    assert pc.tracked_keywords() == ["에어프라이어 추천"]
