#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""kp_merge_visual.py —— 把「视频截图 + 结论/注意」卡片并入各章笔记。

与 kp_merge.py 的分工：
  kp_merge.py           处理纯文本知识点（data/kp/raw/chNN.json），卡片 class="kp k-…"
  kp_merge_visual.py    处理截图知识点（data/kp/raw_visual/chNN.json），卡片 class="kp kvis k-…"

两边互不干扰，各自幂等：本脚本每次先剥掉自己插入的卡片（`aside.kp.kvis` 与章末
`<h3 id="cNkpendvis">`），再按 raw_visual 重新插一遍。

    python scripts/kp_merge_visual.py           # 全部
    python scripts/kp_merge_visual.py 1 12      # 只处理这几章
    python scripts/kp_merge_visual.py --check   # 只校验不写

raw_visual/chNN.json 结构：
    {"n":1,"title":"…","points":[{"kind":"结论","title":"…","text":"…",
      "heading":"一、…","frame":"p01_000205.jpg","src":{"page":1,"t":126}}]}
"""
from __future__ import annotations

import html
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import kp_merge as K

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KP = DATA / "kp"
RAW_VIS = KP / "raw_visual"
FRAMES_SRC = ROOT / "_frames" / "frames"
FRAMES_DST = DATA / "frames"

VIS_RE = __import__("re").compile(
    r'<aside class="kp kvis[\s\S]*?</aside>', __import__("re").S)
VIS_HEAD_RE = __import__("re").compile(r'<h3 class="blk-h2" id="c\d+kpendvis">[\s\S]*?</h3>', __import__("re").S)

KINDS = ("结论", "推论", "注意", "公式", "技巧")

DROP_PATH = KP / "visual_drop.json"


def drop_set() -> set[tuple[int, str]]:
    """人工删除清单：{(章号, 帧文件名)}"""
    if not DROP_PATH.exists():
        return set()
    try:
        items = json.loads(DROP_PATH.read_text(encoding="utf-8")).get("items") or []
    except Exception:
        return set()
    return {(int(it["ch"]), it["frame"]) for it in items if it.get("ch") and it.get("frame")}


def card_html(pt, n, i):
    kind = pt["kind"]
    cls = K.KIND_CLS.get(kind, "tip")
    frame = pt["frame"]
    page = int(pt["src"]["page"])
    t = int(pt["src"]["t"])
    # 截图卡片只显示「标题 + 截图 + 出处」；OCR 文本仅留在 data-ocr 里给搜索用
    ocr = " ".join((pt.get("text") or "").split())
    return (
        f'<aside class="kp kvis k-{cls}" id="kpv-{n}-{i}" data-frame="{html.escape(frame)}" '
        f'data-ocr="{html.escape(ocr)}">'
        f'<div class="kp-h"><span class="kp-kind">{kind}</span>'
        f'<span class="kp-title">{html.escape(pt["title"])}</span>'
        f'<span class="kp-tagvis">视频截图</span></div>'
        f'<img class="kp-shot" src="data/frames/{html.escape(frame)}" '
        f'alt="第 {page} 集 {K.mmss(t)} 板书截图" loading="lazy">'
        f'<a class="kp-src" href="#/video/{page}?t={t}">第 {page} 集 · {K.mmss(t)} ↗</a></aside>')


def process(n: int, videos: dict, check_only: bool) -> dict | None:
    raw_path = RAW_VIS / f"ch{n:02d}.json"
    if not raw_path.exists():
        return None
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    points = raw.get("points") or []

    cpath = DATA / "content" / f"ch{n:02d}.json"
    ch = json.loads(cpath.read_text(encoding="utf-8"))
    notes = next(s for s in ch["sections"] if s["kind"] == "notes")
    toc = notes.get("toc") or []
    eps = {v["page"]: v for v in videos.get(n, [])}

    issues = Counter()
    kept = []
    drop = drop_set()
    for p in points:
        kind = p.get("kind", "")
        title = (p.get("title") or "").strip()
        text = (p.get("text") or "").strip()
        src = p.get("src") or {}
        page, t = int(src.get("page") or 0), int(src.get("t") or 0)
        frame = (p.get("frame") or "").strip()
        if (n, frame) in drop:
            issues["dropped"] += 1
            continue
        if kind not in KINDS or not title or not (6 <= len(text) <= 140):
            issues["invalid"] += 1
            continue
        if page not in eps or not (0 < t <= eps[page]["duration"]):
            issues["invalid"] += 1
            continue
        if not frame or not (FRAMES_SRC / frame).exists():
            issues["no_frame"] += 1
            continue
        nt = K.norm_text(text)
        if any(K.similar(nt, K.norm_text(k["text"])) > 0.75 for k in kept):
            issues["dupe"] += 1
            continue
        anchor, matched = K.resolve_anchor(p.get("heading"), toc)
        if not matched:
            anchor = None
            issues["unmatched"] += 1
        kept.append({"kind": kind, "title": title, "text": text,
                     "anchor": anchor, "frame": frame, "src": {"page": page, "t": t}})

    kept.sort(key=lambda p: (p["src"]["page"], p["src"]["t"]))
    stats = {"n": n, "count": len(kept), "byKind": dict(Counter(p["kind"] for p in kept)),
             **issues}

    if check_only:
        return stats

    body = VIS_RE.sub("", notes["html"])
    body = VIS_HEAD_RE.sub("", body)
    hidx = K.heading_index(body)
    buckets: dict[str, list] = {}
    tail = []
    for i, p in enumerate(kept, 1):
        p["_i"] = i
        if p["anchor"] and p["anchor"] in hidx:
            buckets.setdefault(p["anchor"], []).append(p)
        else:
            tail.append(p)

    out, last = [], 0
    for anchor, (_text, end) in sorted(hidx.items(), key=lambda kv: kv[1][1]):
        if anchor not in buckets:
            continue
        out.append(body[last:end])
        out.append("".join(card_html(p, n, p["_i"]) for p in buckets[anchor]))
        last = end
    out.append(body[last:])
    body = "".join(out)

    if tail:
        body += (f'<h3 class="blk-h2" id="c{n}kpendvis">视频截图补充（板书）</h3>'
                 + "".join(card_html(p, n, p["_i"]) for p in tail))

    notes["html"] = body
    cpath.write_text(json.dumps(ch, ensure_ascii=False, indent=1), encoding="utf-8")

    FRAMES_DST.mkdir(parents=True, exist_ok=True)
    for p in kept:
        src = FRAMES_SRC / p["frame"]
        dst = FRAMES_DST / p["frame"]
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)

    (KP / f"ch{n:02d}_visual.json").write_text(json.dumps(
        {"n": n, "title": ch["title"], "points": kept}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return stats


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    wanted = [int(a) for a in argv if a.isdigit()]
    videos_json = json.loads((DATA / "videos.json").read_text(encoding="utf-8"))["videos"]
    videos: dict[int, list] = {}
    for v in videos_json:
        videos.setdefault(int(v["chapter"]), []).append(v)

    rows, total = [], 0
    for n in sorted(videos):
        if wanted and n not in wanted:
            continue
        st = process(n, videos, check_only)
        if st is None:
            continue
        total += st["count"]
        rows.append(st)
        flag = "检查" if check_only else "写入"
        extra = " ".join(f"{k}={v}" for k, v in st.items() if k not in ("n", "count", "byKind"))
        print(f"ch{n:02d} {flag} {st['count']} 条 {st['byKind']}"
              + (f"  {extra}" if extra else ""))

    if not check_only:
        course_path = DATA / "course.json"
        course = json.loads(course_path.read_text(encoding="utf-8"))
        for c in course["chapters"]:
            st = next((r for r in rows if r["n"] == int(c["n"])), None)
            if not st:
                continue
            c.setdefault("kp", {})["visualCount"] = st["count"]
            c["kp"]["visualByKind"] = st["byKind"]
        course["kpVisualTotal"] = sum(r["count"] for r in rows)
        course_path.write_text(json.dumps(course, ensure_ascii=False, indent=1), encoding="utf-8")
        (KP / "report_visual.json").write_text(json.dumps(
            {"total": total, "chapters": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"合计 {total} 条截图知识点；course.kpVisualTotal 已更新")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
