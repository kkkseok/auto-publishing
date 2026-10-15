"""초안 시험 생성 — 조사 1회를 여러 블로그가 공유한다.

    python -m tools.draft_trial "<키워드>" A C        # A·C 두 편, 조사 1회
    python -m tools.draft_trial "<키워드>" B D

data/drafts/<날짜>_<키워드>/ 에 facts.json(공유) 과 블로그별 <블로그>_draft.json ·
<블로그>_preview.html, 캡처 이미지를 남긴다. 같은 폴더에 facts.json 이 있으면 조사를
다시 하지 않는다. 발행은 하지 않는다.
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

_PAGE = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>'
         '<style>body{{max-width:760px;margin:24px auto;padding:0 16px;font-family:"Malgun Gothic",sans-serif;'
         'background:#fff;color:#222;word-break:keep-all}} h1{{font-size:26px;line-height:1.4}}</style>'
         '</head><body><h1>{title}</h1>{body}<p style="color:#888">{tags}</p></body></html>')


def main(keyword: str, blogs: list[str]) -> None:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "", keyword)[:20]
    out = Path("data/drafts") / f"{date.today():%y%m%d}_{slug}"
    out.mkdir(parents=True, exist_ok=True)
    facts_path = out / "facts.json"
    if facts_path.exists():
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        print(f"[조사] 기존 사실 시트 재사용 ({len(facts['cited_urls'])} 출처)", flush=True)
    else:
        print(f"[조사] {keyword}", flush=True)
        facts = research(keyword)
        facts_path.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"       출처 {len(facts['cited_urls'])}개 · in {facts['usage'].get('input_tokens')} "
              f"out {facts['usage'].get('output_tokens')}", flush=True)
    shots: dict[str, str] = {}
    for blog in blogs:
        print(f"[작성] {blog}", flush=True)
        draft = write(keyword, blog, facts)
        (out / f"{blog}_draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
        new = [u for u in draft["capture_urls"] if u not in shots]
        if new:
            shots.update(capture(new, out, limit=4))
        imgs = {u: Path(p).name for u, p in shots.items() if u in draft["capture_urls"]}
        page = _PAGE.format(title=draft["title"], body=render_html(draft, imgs, blog),
                            tags=" ".join("#" + t for t in draft["tags"]))
        (out / f"{blog}_preview.html").write_text(page, encoding="utf-8")
        u = draft["_usage"]
        print(f"       → {blog}_preview.html · in {u.get('input_tokens')} (캐시 읽기 "
              f"{u.get('cache_read_input_tokens')}) out {u.get('output_tokens')}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], [b.upper() for b in sys.argv[2:]] or ["A"])
