"""유입 분석 — 어떤 글·주제가 읽히는지 판단하고, 그 결과를 다음 글과 기존 글 수정에 넘긴다.

입력: data/traffic.db (pipelines/traffic_collect 가 매일 채움), data/coupang_roi.json (쿠팡 subId 실적)
출력: data/content_insights.json — 아래 세 곳이 읽는다.
  - common/keyword_planner.score()  → topic_multiplier(): 읽히는 씨앗·카테고리는 가중, 안 읽히면 감점
                                       opportunity_bonus(): 실제 검색어로 확인된 수요 키워드 우대
  - common/draft_writer.write()     → writer_hints(): 잘 된 제목 패턴·실제 검색어를 작성 프롬프트에
  - pipelines/content_revise        → revision_candidates: 고칠 만한 기존 글

글 상태 (발행 후 경과일 기준, 최근 28일 합계):
  new        14일 미만 — 판단 보류 (새 글은 색인·순위가 자리 잡는 데 2~4주)
  dead       21일 이상인데 노출·조회 0 — 수요 없음 또는 색인 실패
  low_ctr    구글 노출 100+ 인데 CTR 2% 미만 — 제목·설명 문제
  striking   구글 평균 순위 8~20위, 노출 30+ — 본문 보강하면 1페이지 가능
  performing 방문(구글 클릭 + 네이버 조회)이 같은 유형 글 평균 이상
  weak       그 밖 (조금 읽히지만 평균 미만)

주제 가중치는 같은 유형(info/price) 평균 대비 비율을 1 쪽으로 반쯤 당겨(0.5~2.0) 쓴다 —
글 몇 개로 주제를 영영 끊지 않도록. 14일 넘은 글이 없으면 전부 1.0.
"""
from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from common import traffic_db
from common.keyword_planner import seed_of

_ROOT = Path(__file__).resolve().parent.parent
PATH = _ROOT / "data" / "content_insights.json"
ROI = _ROOT / "data" / "coupang_roi.json"
REVISIONS = _ROOT / "data" / "revisions.json"

WINDOW_DAYS = 28
MATURE_DAYS = 14
DEAD_DAYS = 21
LOW_CTR_IMPRESSIONS, LOW_CTR = 100, 0.02
STRIKING_POS, STRIKING_IMPRESSIONS = (8.0, 20.0), 30
REVISE_COOLDOWN_DAYS = 30
MULT_RANGE = (0.5, 2.0)


def _age(published: str, today: date) -> int:
    try:
        return (today - date.fromisoformat(published[:10])).days
    except ValueError:
        return 0


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _subid_of(post: dict) -> str:
    """가격형 글의 쿠팡 subId — 초안 폴더의 <블로그>_products.json 에 있다."""
    p = Path(post.get("draft_dir") or "") / f"{post['blog']}_products.json"
    try:
        prods = json.loads(p.read_text(encoding="utf-8"))
        return prods[0].get("subid", "") if prods else ""
    except Exception:
        return ""


def _coupang_by_sub(since: str) -> dict[str, dict]:
    try:
        daily = json.loads(ROI.read_text(encoding="utf-8")).get("daily", {})
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for day, subs in daily.items():
        if day < since:
            continue
        for sid, st in subs.items():
            acc = out.setdefault(sid, {"clicks": 0, "orders": 0, "commission": 0})
            for k in acc:
                acc[k] += int(st.get(k) or 0)
    return out


def _recently_revised(today: date) -> set[str]:
    try:
        revs = json.loads(REVISIONS.read_text(encoding="utf-8"))
    except Exception:
        return set()
    since = (today - timedelta(days=REVISE_COOLDOWN_DAYS)).isoformat()
    return {r["url"] for r in revs.values() if r.get("created", "") >= since}


def _naver_queries_for(conn, blog: str, keyword: str, seed: str, since: str) -> list[dict]:
    """네이버 유입 검색어는 블로그 단위 — 글의 씨앗·키워드가 들어간 검색어만 그 글 것으로 본다."""
    keys = [k for k in {_norm(seed), _norm(keyword)} if k]
    rows = traffic_db.top_queries(conn, f"naver:{blog}", since, limit=200)
    return [r for r in rows if any(k in _norm(r["query"]) for k in keys)][:10]


def classify(p: dict, kind_avg: float) -> str:
    if p["age"] < MATURE_DAYS:
        return "new"
    imp, clk, views = p["impressions"], p["clicks"], p["views"]
    if imp + views == 0:
        return "dead" if p["age"] >= DEAD_DAYS else "new"
    if imp >= LOW_CTR_IMPRESSIONS and clk / imp < LOW_CTR:
        return "low_ctr"
    pos = p["position"]
    if pos is not None and STRIKING_POS[0] <= pos <= STRIKING_POS[1] and imp >= STRIKING_IMPRESSIONS:
        return "striking"
    return "performing" if kind_avg and p["visits_per_day"] >= kind_avg else "weak"


def _multipliers(posts: list[dict], kind: str, field: str) -> dict[str, dict]:
    mature = [p for p in posts if p["kind"] == kind and p["age"] >= MATURE_DAYS]
    if not mature:
        return {}
    avg = sum(p["visits_per_day"] for p in mature) / len(mature)
    groups: dict[str, list[dict]] = {}
    for p in mature:
        if p.get(field):
            groups.setdefault(p[field], []).append(p)
    out = {}
    for key, ps in groups.items():
        g = sum(p["visits_per_day"] for p in ps) / len(ps)
        mult = 1.0 if avg == 0 else (g + avg) / (2 * avg)
        out[key] = {"posts": len(ps), "visits_per_day": round(g, 3),
                    "multiplier": round(min(max(mult, MULT_RANGE[0]), MULT_RANGE[1]), 2)}
    return out


def _opportunities(conn, posts: list[dict], since: str) -> dict[str, list[dict]]:
    """검색어로 확인된 수요 중 아직 전용 글이 없는 것 — 다음 키워드 후보."""
    have = {_norm(p["keyword"]) for p in posts}
    kind_of = {p["url"]: p["kind"] for p in posts}
    out: dict[str, dict[str, dict]] = {"info": {}, "price": {}}
    rows = conn.execute(
        "SELECT url, query, SUM(impressions) imp, SUM(clicks) clk, SUM(views) views FROM queries "
        "WHERE day>=? GROUP BY url, query", (since,)).fetchall()
    for r in rows:
        url, q = r["url"], r["query"]
        kind = ("price" if url.endswith(("B", "D")) else "info") if url.startswith("naver:") else kind_of.get(url)
        # 블로그 단위 검색어·옛 글 검색어엔 지금 주제와 무관한 것이 섞인다 — 씨앗에 속한 것만
        if not kind or _norm(q) in have or len(q) < 3 or not seed_of(q, kind):
            continue
        acc = out[kind].setdefault(q, {"query": q, "impressions": 0, "clicks": 0, "views": 0})
        acc["impressions"] += r["imp"] or 0
        acc["clicks"] += r["clk"] or 0
        acc["views"] += r["views"] or 0
    return {k: sorted(v.values(), key=lambda x: x["impressions"] + x["views"] * 10, reverse=True)[:30]
            for k, v in out.items()}


def build(today: date | None = None) -> str:
    """분석해서 data/content_insights.json 을 쓰고, 한 줄 요약을 돌려준다."""
    today = today or date.today()
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    coupang = _coupang_by_sub(since)
    revised = _recently_revised(today)
    with closing(traffic_db.connect()) as conn:
        posts = []
        for p in traffic_db.posts(conn):
            t = traffic_db.totals(conn, p["url"], since)
            age = _age(p["published"], today)
            visits = t["clicks"] + t["views"]
            queries = (traffic_db.top_queries(conn, p["url"], since) if p["blog"] in ("C", "D")
                       else _naver_queries_for(conn, p["blog"], p["keyword"], p["seed"], since))
            sid = _subid_of(p) if p["kind"] == "price" else ""
            posts.append({**{k: p[k] for k in ("url", "blog", "kind", "keyword", "seed", "category", "title",
                                               "published", "draft_id")},
                          "age": age, **t, "visits_per_day": round(visits / max(min(age, WINDOW_DAYS), 1), 3),
                          "ctr": round(t["clicks"] / t["impressions"], 4) if t["impressions"] else None,
                          "coupang": coupang.get(sid, {}) if sid else {}, "top_queries": queries})
        opps = _opportunities(conn, posts, since)
    kind_avg = {}
    for kind in ("info", "price"):
        mature = [p for p in posts if p["kind"] == kind and p["age"] >= MATURE_DAYS]
        kind_avg[kind] = sum(p["visits_per_day"] for p in mature) / len(mature) if mature else 0.0
    for p in posts:
        p["status"] = classify(p, kind_avg[p["kind"]])
    candidates = [{"url": p["url"], "draft_id": p["draft_id"], "blog": p["blog"], "reason": p["status"],
                   "title": p["title"]}
                  for p in sorted(posts, key=lambda x: x["impressions"], reverse=True)
                  if p["status"] in ("low_ctr", "striking") and p["url"] not in revised]
    data = {
        "updated": today.isoformat(), "window_days": WINDOW_DAYS,
        "kind_avg_visits_per_day": {k: round(v, 3) for k, v in kind_avg.items()},
        "multipliers": {kind: {"seed": _multipliers(posts, kind, "seed"),
                               "category": _multipliers(posts, kind, "category")} for kind in ("info", "price")},
        "opportunities": opps,
        "revision_candidates": candidates,
        "posts": sorted(posts, key=lambda x: (x["clicks"] + x["views"], x["impressions"]), reverse=True),
    }
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)
    _CACHE.clear()
    counts: dict[str, int] = {}
    for p in posts:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return "상태 " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f" · 수정 후보 {len(candidates)}"


# ── 다른 모듈이 읽는 부분 ────────────────────────────────────────────────

_CACHE: dict[str, dict] = {}


def load() -> dict:
    if "d" not in _CACHE:
        try:
            _CACHE["d"] = json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            _CACHE["d"] = {}
    return _CACHE["d"]


def topic_multiplier(kind: str, seed: str, category: str) -> float:
    """씨앗 성과가 있으면 씨앗, 없으면 카테고리 가중치. 데이터가 없으면 1.0."""
    m = load().get("multipliers", {}).get(kind, {})
    if seed and seed in m.get("seed", {}):
        return m["seed"][seed]["multiplier"]
    if category and category in m.get("category", {}):
        return m["category"][category]["multiplier"]
    return 1.0


def opportunity_bonus(kind: str, keyword: str) -> float:
    """우리 글에 실제로 들어온 검색어와 같은 키워드면 1.3 — 수요가 확인된 것."""
    k = _norm(keyword)
    return 1.3 if any(_norm(o["query"]) == k for o in load().get("opportunities", {}).get(kind, [])) else 1.0


def writer_hints(blog: str, keyword: str, seed: str = "", category: str = "") -> str:
    """작성 프롬프트에 붙일 운영 데이터. 쓸 만한 데이터가 없으면 ''."""
    data = load()
    posts = [p for p in data.get("posts", []) if p["blog"] == blog and p["age"] >= MATURE_DAYS]
    lines = []
    good = [p for p in posts if p["status"] in ("performing", "striking") and (p["clicks"] + p["views"]) > 0][:5]
    if good:
        lines.append("이 블로그에서 실제로 방문이 많았던 글 제목 (제목 구조·도입 방식 참고, 문장 복제 금지):")
        lines += [f"- {p['title']} (방문 {p['clicks'] + p['views']}, CTR {p['ctr'] if p['ctr'] is not None else '-'})"
                  for p in good]
    weak = [p for p in posts if p["status"] == "low_ctr"][:3]
    if weak:
        lines.append("노출은 많았지만 클릭이 적었던 제목 (이런 제목은 피한다):")
        lines += [f"- {p['title']}" for p in weak]
    related = [q for p in data.get("posts", [])
               if p["blog"] == blog and ((seed and p["seed"] == seed) or (category and p["category"] == category))
               for q in p.get("top_queries", [])]
    related += [o for o in data.get("opportunities", {}).get("price" if blog in ("B", "D") else "info", [])
                if seed and _norm(seed) in _norm(o["query"])]
    seen, qs = set(), []
    for q in related:
        if q["query"] not in seen:
            seen.add(q["query"])
            qs.append(q["query"])
    if qs:
        lines.append("같은 주제로 실제 검색해서 들어온 검색어 (소제목·FAQ 에서 이 질문들에 답한다):")
        lines += [f"- {q}" for q in qs[:12]]
    return "\n".join(lines)


def summary_text(limit: int = 5) -> str:
    """주간 리포트용 — 상위 글, 수정 후보, 죽은 주제."""
    data = load()
    if not data:
        return ""
    posts = data.get("posts", [])
    lines = [f"📈 유입 (최근 {data.get('window_days')}일, 기준 {data.get('updated')})"]
    top = [p for p in posts if p["clicks"] + p["views"] > 0][:limit]
    if top:
        lines.append("방문 상위 글:")
        lines += [f"  {p['blog']} {p['title'][:28]} — 방문 {p['clicks'] + p['views']} · 노출 {p['impressions']}"
                  + (f" · 순위 {p['position']}" if p['position'] else "") for p in top]
    else:
        lines.append("방문이 기록된 글 없음")
    counts: dict[str, int] = {}
    for p in posts:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    lines.append("글 상태: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for kind in ("info", "price"):
        seeds = data.get("multipliers", {}).get(kind, {}).get("seed", {})
        up = [s for s, v in seeds.items() if v["multiplier"] >= 1.3]
        down = [s for s, v in seeds.items() if v["multiplier"] <= 0.7]
        if up or down:
            lines.append(f"{kind} 주제: 늘림 {', '.join(up) or '-'} / 줄임 {', '.join(down) or '-'}")
    opp = [o["query"] for k in ("info", "price") for o in data.get("opportunities", {}).get(k, [])[:3]]
    if opp:
        lines.append("새 글 후보 검색어: " + ", ".join(opp))
    cands = data.get("revision_candidates", [])
    if cands:
        lines.append(f"수정 후보 {len(cands)}개: " + ", ".join(f"{c['blog']} {c['title'][:18]}({c['reason']})"
                                                          for c in cands[:3]))
    return "\n".join(lines)
