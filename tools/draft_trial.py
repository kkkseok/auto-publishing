"""초안 1편 시험 생성: python -m tools.draft_trial <블로그 A|C> "<키워드>"

data/drafts/<날짜>_<블로그>_<키워드>/ 에 facts.json · draft.json · preview.html · 캡처 이미지를 남긴다.
발행은 하지 않는다.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from common.draft_writer import render_html, research, write
from common.page_capture import capture

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


def main(blog: str, keyword: str) -> None:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "", keyword)[:20]
    out = Path("data/drafts") / f"{date.today():%y%m%d}_{blog}_{slug}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[1/4] 조사: {keyword}", flush=True)
    facts = research(keyword)
    (out / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"      출처 {len(facts['cited_urls'])}개", flush=True)
    print("[2/4] 작성", flush=True)
    draft = write(keyword, blog, facts)
    (out / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[3/4] 캡처 {len(draft['capture_urls'])}개", flush=True)
    shots = capture(draft["capture_urls"], out)
    imgs = {u: Path(p).name for u, p in shots.items()}
    body = render_html(draft, imgs)
    page = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{draft["title"]}</title>'
            f'<style>body{{max-width:760px;margin:24px auto;padding:0 16px;font-family:"Malgun Gothic",sans-serif;'
            f'line-height:1.75;background:#fff;color:#222}}</style></head><body>'
            f"<h1>{draft['title']}</h1>{body}"
            f"<p style='color:#888'>태그: {' '.join('#' + t for t in draft['tags'])}</p></body></html>")
    (out / "preview.html").write_text(page, encoding="utf-8")
    u1, u2 = facts["usage"], draft["_usage"]
    print(f"[4/4] 완료 → {out}  (토큰 조사 in {u1.get('input_tokens')} out {u1.get('output_tokens')} / "
          f"작성 in {u2.get('input_tokens')} out {u2.get('output_tokens')})", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
